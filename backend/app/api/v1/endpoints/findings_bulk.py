"""Bulk finding operations (v2.234.0).

The Findings page could already multi-select, but had no bulk endpoint at
all: a bulk status change fired one PATCH per finding from the browser via
``Promise.allSettled`` — unbounded, partially-failable, and with no single
audit moment. Ownership was worse, editable only by opening each finding's
detail page, so assigning a 200-finding unowned queue meant 200 round trips.

Mirrors ``host_bulk.py``: an explicit id list, every id validated against the
project before anything is touched, a batch cap, one commit, and a single
summary notification for the assignee instead of N pings.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding
from app.db.models_project import Notification, Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.services.finding_service import FindingService

router = APIRouter(dependencies=[Depends(get_current_user)])

# Mirror of host_bulk._BULK_CAP — the most findings one call may touch.
_BULK_CAP = 5000


class BulkResult(BaseModel):
    """``requested`` vs ``affected`` are reported separately so a caller can
    see that some ids were dropped (not in this project / already gone)
    instead of assuming the whole batch landed."""
    affected: int
    requested: int
    # Ids that were rejected, so the UI can say which rather than just a count.
    skipped_ids: List[int] = Field(default_factory=list)


def _valid_findings(db: Session, project_id: int, finding_ids: List[int]) -> List[Finding]:
    """Filter to findings that actually belong to this project. Rejects an
    oversized batch outright rather than silently truncating."""
    if not finding_ids:
        return []
    if len(finding_ids) > _BULK_CAP:
        raise HTTPException(
            status_code=413,
            detail=f"Too many findings in one bulk operation (max {_BULK_CAP})",
        )
    return (
        db.query(Finding)
        .filter(Finding.project_id == project_id, Finding.id.in_(set(finding_ids)))
        .all()
    )


class BulkStatusRequest(BaseModel):
    finding_ids: List[int]
    status: str
    # Terminal dispositions require a justification; the service enforces it.
    summary: Optional[str] = Field(default=None, max_length=4000)


@router.post(
    "/findings/bulk/status",
    response_model=BulkResult,
    summary="Set status on many findings",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def bulk_status(
    payload: BulkStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Apply one status to many findings in a single transaction.

    The justification requirement for terminal statuses is enforced per
    finding by ``FindingService.set_status``, so a bulk terminal move without
    a summary fails the whole batch rather than dispositioning some records
    unjustified — the audit trail stays honest either way.
    """
    findings = _valid_findings(db, project.id, payload.finding_ids)
    if not findings:
        return BulkResult(affected=0, requested=len(payload.finding_ids), skipped_ids=payload.finding_ids)

    svc = FindingService(db)
    for finding in findings:
        svc.set_status(
            finding=finding,
            status=payload.status,
            actor_id=current_user.id,
            summary=payload.summary,
        )
    db.commit()

    found = {f.id for f in findings}
    return BulkResult(
        affected=len(findings),
        requested=len(payload.finding_ids),
        skipped_ids=[i for i in payload.finding_ids if i not in found],
    )


class BulkAssignRequest(BaseModel):
    finding_ids: List[int]
    # None = unassign. The single-finding PATCH can't express this (it skips
    # the field when owner_id is None), so bulk is the only way to clear an
    # owner in one action.
    assignee_user_id: Optional[int] = None


@router.post(
    "/findings/bulk/assign",
    response_model=BulkResult,
    summary="Assign (or unassign) many findings to one user",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def bulk_assign(
    payload: BulkAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    findings = _valid_findings(db, project.id, payload.finding_ids)
    if not findings:
        return BulkResult(affected=0, requested=len(payload.finding_ids), skipped_ids=payload.finding_ids)

    assignee: Optional[User] = None
    if payload.assignee_user_id is not None:
        assignee = (
            db.query(User)
            .filter(User.id == payload.assignee_user_id, User.is_active.is_(True))
            .first()
        )
        if not assignee:
            raise HTTPException(status_code=404, detail="Assignee not found")
        # Don't hand work to someone who can't open the project.
        if assignee.role != UserRole.ADMIN:
            is_member = (
                db.query(ProjectMembership)
                .filter(
                    ProjectMembership.project_id == project.id,
                    ProjectMembership.user_id == assignee.id,
                )
                .first()
            )
            if not is_member:
                raise HTTPException(
                    status_code=400, detail="Assignee is not a member of this project",
                )

    for finding in findings:
        finding.owner_id = assignee.id if assignee else None

    # One summary notification for the batch, not N pings.
    if assignee is not None and assignee.id != current_user.id:
        n = len(findings)
        db.add(
            Notification(
                user_id=assignee.id,
                project_id=project.id,
                type="assignment",
                title=f"{n} finding{'s' if n != 1 else ''} assigned to you",
                body=(
                    f"@{current_user.username} assigned {n} finding"
                    f"{'s' if n != 1 else ''} to you in '{project.name}'"
                ),
                source_type="project",
                source_id=project.id,
                actor_id=current_user.id,
            )
        )

    db.commit()
    found = {f.id for f in findings}
    return BulkResult(
        affected=len(findings),
        requested=len(payload.finding_ids),
        skipped_ids=[i for i in payload.finding_ids if i not in found],
    )
