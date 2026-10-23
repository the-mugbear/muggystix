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
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import check_permissions
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingStatus, FindingStatusHistory
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_vulnerability import Vulnerability
from app.services.cvss_service import CvssError, normalize_cvss
from app.services.finding_service import FindingService
from app.services.report_images import normalise_references
from app.services.report_text import REPORT_TEXT_FIELDS


@dataclass
class FindingActor:
    """Who is acting, as far as authored-content rights go (v2.375.0).

    A finding's title and report text are any project analyst's (2.519.0,
    owner 2026-10-21): any analyst may set a finding's owner, so a rule of
    "the author or the owner" was one request away from "any analyst" and
    only got in the way — an analyst given a finding someone else registered
    could not edit it, while its proposals were sent to them.  Deleting the
    finding stays its author's or a project admin's."""
    user_id: int
    is_project_admin: bool
    is_project_analyst: bool

    def _is(self, user_id: Optional[int]) -> bool:
        return user_id is not None and user_id == self.user_id

    def may_modify(self, finding: Finding) -> bool:
        return self.is_project_analyst

    def may_delete(self, finding: Finding) -> bool:
        return self.is_project_admin or self._is(finding.created_by_id)


def finding_actor(db: Session, project_id: int, user: User) -> FindingActor:
    if user.role == UserRole.ADMIN:
        return FindingActor(user_id=user.id, is_project_admin=True, is_project_analyst=True)
    role = (
        db.query(ProjectMembership.role)
        .filter(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user.id)
        .scalar()
    )
    return FindingActor(
        user_id=user.id,
        is_project_admin=bool(role) and check_permissions(role, ProjectRole.ADMIN.value),
        is_project_analyst=bool(role) and check_permissions(role, ProjectRole.ANALYST.value),
    )


def require_modify(actor: FindingActor, finding: Finding, what: str) -> None:
    if not actor.may_modify(finding):
        raise HTTPException(
            status_code=403,
            detail=f"Only a project analyst or admin can {what}.",
        )


def require_delete(actor: FindingActor, finding: Finding) -> None:
    if not actor.may_delete(finding):
        raise HTTPException(
            status_code=403,
            detail="Only the finding's author or a project admin can delete it.",
        )


FIELD_LABELS = {
    "title": "Title", "severity": "Severity", "owner": "Owner",
    "description": "Description", "impact": "Impact", "recommendation": "Recommendation",
    "references": "References", "steps_to_reproduce": "Steps to reproduce", "evidence_text": "Evidence",
    "cvss_vector": "CVSS vector", "cvss_score": "CVSS score",
}


def _shown(value: object) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


def record_edit(
    db: Session, finding: Finding, *, field: str, before: object, after: object,
    actor_id: Optional[int], summary: Optional[str] = None,
) -> None:
    """One line on the finding's history for one edited field, keeping its
    value before and after (2.519.0, owner 2026-10-21: an edit was recorded
    nowhere, and any analyst may now make one).  The ONE writer of an edit
    row — the PATCH, the bulk assign and an accepted proposal all come here.
    ``before`` / ``after`` are the values as a reader knows them (an owner's
    name, a severity's label).  The finding's status does not move.  Added,
    not committed."""
    label = FIELD_LABELS.get(field, field)
    before_s, after_s = _shown(before), _shown(after)
    if summary is None:
        if field in ("severity", "owner"):
            summary = f"{label}: {before_s or 'none'} → {after_s or 'none'}"
        elif field == "title":
            summary = "Renamed"
        else:
            summary = f"{label} {'written' if before_s is None else 'cleared' if after_s is None else 'edited'}"
    db.add(FindingStatusHistory(
        finding_id=finding.id, from_status=finding.status, to_status=finding.status,
        changed_by_id=actor_id, summary=summary,
        field=field, before_value=before_s, after_value=after_s,
    ))


def edit_values(db: Session, finding_id: int, entry_id: int) -> FindingStatusHistory:
    """One edit's line on the finding's history, with its values — the ONE
    read behind the page's and the agents' route (2.519.0); 404 for a line
    that is not an edit, or another finding's.  The caller has checked the
    finding is in the project."""
    row = (
        db.query(FindingStatusHistory)
        .filter(
            FindingStatusHistory.id == entry_id, FindingStatusHistory.finding_id == finding_id,
            FindingStatusHistory.field.isnot(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No such edit on this finding.")
    return row


def severity_label(severity: object) -> Optional[str]:
    value = getattr(severity, "value", severity)
    return str(value).capitalize() if value else None


def _person(db: Session, user_id: Optional[int]) -> Optional[str]:
    user = db.get(User, user_id) if user_id is not None else None
    return (user.full_name or user.username) if user else None


def set_owner(db: Session, finding: Finding, owner_id: Optional[int], actor_id: Optional[int]) -> None:
    """Give the finding to ``owner_id`` (already checked by the assignee
    rule) and record it — the PATCH and the bulk assign alike."""
    if owner_id == finding.owner_id:
        return
    record_edit(
        db, finding, field="owner", before=_person(db, finding.owner_id), after=_person(db, owner_id),
        actor_id=actor_id,
    )
    finding.owner_id = owner_id


def assign_findings(
    db: Session, project, findings: List[Finding], owner_id: Optional[int], actor_id: Optional[int],
) -> List[Finding]:
    """Give ``findings`` to ``owner_id`` (already checked by the assignee
    rule, ``deps.load_project_assignee``) and tell them — the ONE assign, for
    the finding's ``PATCH`` and the bulk assign (review 2026-10-10 R1c: the
    PATCH assigned silently while the bulk route notified).  Each change gets
    its history line (``set_owner``); the new owner gets ONE notification for
    the findings that actually changed hands, none when nothing changed or they
    took the findings themself.  Returns the findings that changed; the caller
    commits.  The findings' locks come first (id order) and the owner each
    had is read under them (review 2026-10-23, R-TR1)."""
    from app.services.notification_service import NotificationService

    FindingService(db).lock_findings(findings)
    changed = [f for f in findings if f.owner_id != owner_id]
    for finding in changed:
        set_owner(db, finding, owner_id, actor_id)
    if owner_id is not None and changed:
        actor = db.get(User, actor_id) if actor_id is not None else None
        NotificationService(db).notify_findings_assigned(
            assignee_id=owner_id, project=project, findings=changed, actor=actor,
        )
    return changed


def apply_report_text(
    db: Session, finding: Finding, actor: FindingActor, sent: Dict[str, Optional[object]], *,
    provenance: Optional[str] = None,
) -> Dict[str, object]:
    """Apply report-text fields (``REPORT_TEXT_FIELDS``, ``cvss_vector``,
    ``cvss_score``) that are present in ``sent``.  Only a field whose value
    actually changes needs the right, so resending an unchanged form after a
    triage edit is not refused.  Each changed field gets its history line (``record_edit``); ``provenance`` (an accepted proposal's
    "from agent proposal #…") goes ON that line — "<Field> set <provenance>"
    — never on a second one.  Returns the applied changes; the caller
    commits."""
    changes: Dict[str, object] = {}
    for field in REPORT_TEXT_FIELDS:
        if field in sent:
            # Stored in the ONE placement grammar: a tolerated spelling of an
            # image reference (`(<evidence:57>)`…) is rewritten here, on every
            # save — a person's and an accepted proposal's alike — so what
            # "placed" means is the same for the editor, the delete guard and
            # the report.  Text with no reference is unchanged.
            value = normalise_references((str(sent[field] or "")).strip()) or None
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
            record_edit(
                db, finding, field=field, before=getattr(finding, field), after=value,
                actor_id=actor.user_id,
                summary=f"{FIELD_LABELS[field]} set {provenance}" if provenance else None,
            )
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
    on_join: str = "set",
) -> Finding:
    """Promote a scanner observation to a finding, or dismiss it.

    v2.360.0 — a false-positive dismissal is about THIS host unless the caller
    says the whole issue.  v2.366.0 — a PROMOTION may be too (``scope:
    "host"``): "confirmed" is then recorded for the host that was looked at,
    not for every host carrying the issue.  The default for a promotion stays
    "issue".  Accepted risk is issue-wide: a decision about the issue, not a
    host.  The caller commits.

    ``on_join="confirm"`` (review 2026-10-01 R9) is for a caller that reports
    ONE host's result rather than a judgment of the issue — a test's
    evidence.  Joining an existing finding then changes its status only from
    open / retest to confirmed (see ``FindingService.promote_vulnerability``).
    The promote / dismiss click and an accepted proposal leave it at ``"set"``.

    A promotion scoped to the host says the issue is real THERE, so an
    endpoint of the finding on that host that was dismissed as a false
    positive goes back to open; an issue-wide promotion leaves every host's
    own dismissal alone.
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
    on_this_host = scope == "host"
    # The evidence lock that must precede the finding, and the linking of the
    # test results that showed the issue, are the service's own steps.
    finding, _created = svc.promote_vulnerability(
        vuln=vuln, project_id=project_id, actor_id=actor_id,
        severity=severity, status=status, owner_id=owner_id, summary=summary,
        host_ids=([vuln.host_id] if vuln.host_id else []) if on_this_host else None,
        on_join=on_join,
    )
    if on_this_host and not is_fp and vuln.host_id:
        svc.reopen_false_positive_endpoints(
            finding=finding, host_id=vuln.host_id, actor_id=actor_id, note="promoted on this host",
        )
    return finding
