"""Bulk host operations (v2.71.0).

Apply tags / assignment / follow-status to many hosts at once — the
backend half of the Hosts-page multi-select bulk-action bar.  Each
endpoint takes an explicit ``host_ids`` list (the client gets the full
"select all matching" set from ``GET /hosts/ids``) and validates every
id belongs to the project before touching anything.  Writes are batched
into a single commit rather than one-commit-per-host.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models import HostFollow, FollowStatus, HostTag, HostTagAssignment
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, Notification, ProjectRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role

router = APIRouter(dependencies=[Depends(get_current_user)])

# Mirror of hosts._BULK_SELECT_CAP — the most hosts one bulk call touches.
_BULK_CAP = 5000


class BulkResult(BaseModel):
    affected: int
    requested: int


def _valid_host_ids(db: Session, project_id: int, host_ids: List[int]) -> List[int]:
    """Filter the requested ids down to hosts that actually belong to the
    project.  Rejects oversized batches outright."""
    if not host_ids:
        return []
    if len(host_ids) > _BULK_CAP:
        raise HTTPException(status_code=413, detail=f"Too many hosts in one bulk operation (max {_BULK_CAP})")
    rows = (
        db.query(models.Host.id)
        .filter(models.Host.project_id == project_id, models.Host.id.in_(host_ids))
        .all()
    )
    return [r[0] for r in rows]


class BulkTagRequest(BaseModel):
    host_ids: List[int]
    tag_ids: List[int] = Field(default_factory=list)
    names: List[str] = Field(default_factory=list)
    action: str = Field("add", pattern="^(add|remove)$")


@router.post(
    "/bulk/tags", response_model=BulkResult, summary="Add or remove tags on many hosts",
    # Bulk shared-state mutation — analyst+ only (viewer/auditor are read-only).
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def bulk_tags(
    payload: BulkTagRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host_ids = _valid_host_ids(db, project.id, payload.host_ids)
    if not host_ids:
        return BulkResult(affected=0, requested=len(payload.host_ids))

    tag_ids: set[int] = set()
    for tid in payload.tag_ids:
        tag = db.query(HostTag).filter(HostTag.id == tid, HostTag.project_id == project.id).first()
        if tag:
            tag_ids.add(tag.id)

    # create-by-name only makes sense when adding
    if payload.action == "add":
        for raw in payload.names:
            name = (raw or "").strip()
            if not name:
                continue
            existing = (
                db.query(HostTag)
                .filter(HostTag.project_id == project.id, func.lower(HostTag.name) == name.lower())
                .first()
            )
            if existing:
                tag_ids.add(existing.id)
            else:
                tag = HostTag(project_id=project.id, name=name, created_by_id=current_user.id)
                db.add(tag)
                db.flush()
                tag_ids.add(tag.id)

    if not tag_ids:
        return BulkResult(affected=0, requested=len(host_ids))

    affected = 0
    if payload.action == "add":
        existing_pairs = {
            (h, t)
            for h, t in db.query(HostTagAssignment.host_id, HostTagAssignment.tag_id)
            .filter(HostTagAssignment.host_id.in_(host_ids), HostTagAssignment.tag_id.in_(tag_ids))
            .all()
        }
        for h in host_ids:
            for t in tag_ids:
                if (h, t) not in existing_pairs:
                    db.add(HostTagAssignment(host_id=h, tag_id=t, created_by_id=current_user.id))
                    affected += 1
    else:
        affected = (
            db.query(HostTagAssignment)
            .filter(HostTagAssignment.host_id.in_(host_ids), HostTagAssignment.tag_id.in_(tag_ids))
            .delete(synchronize_session=False)
        )

    db.commit()
    return BulkResult(affected=affected, requested=len(host_ids))


class BulkAssignRequest(BaseModel):
    host_ids: List[int]
    assignee_user_id: int


@router.post(
    "/bulk/assign", response_model=BulkResult, summary="Assign many hosts to one user",
    # Bulk shared-state mutation — analyst+ only (viewer/auditor are read-only).
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def bulk_assign(
    payload: BulkAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host_ids = _valid_host_ids(db, project.id, payload.host_ids)
    if not host_ids:
        return BulkResult(affected=0, requested=len(payload.host_ids))

    assignee = db.query(User).filter(
        User.id == payload.assignee_user_id, User.is_active.is_(True)
    ).first()
    if not assignee:
        raise HTTPException(status_code=404, detail="Assignee not found")
    if assignee.role != UserRole.ADMIN:
        is_member = db.query(ProjectMembership).filter(
            ProjectMembership.project_id == project.id,
            ProjectMembership.user_id == assignee.id,
        ).first()
        if not is_member:
            raise HTTPException(status_code=400, detail="Assignee is not a member of this project")

    now = datetime.now(timezone.utc)
    existing = {
        f.host_id: f
        for f in db.query(HostFollow)
        .filter(HostFollow.user_id == assignee.id, HostFollow.host_id.in_(host_ids))
        .all()
    }
    for h in host_ids:
        follow = existing.get(h)
        if follow:
            follow.assigned_by_id = current_user.id
            follow.assigned_at = now
            follow.status = FollowStatus.IN_REVIEW
        else:
            db.add(HostFollow(
                host_id=h,
                user_id=assignee.id,
                status=FollowStatus.IN_REVIEW,
                assigned_by_id=current_user.id,
                assigned_at=now,
            ))

    # One summary notification for the whole batch (not N pings).
    if assignee.id != current_user.id:
        db.add(Notification(
            user_id=assignee.id,
            project_id=project.id,
            type="assignment",
            title=f"{len(host_ids)} host{'s' if len(host_ids) != 1 else ''} assigned to you",
            body=f"@{current_user.username} assigned {len(host_ids)} host{'s' if len(host_ids) != 1 else ''} to you in '{project.name}'",
            source_type="project",
            source_id=project.id,
            actor_id=current_user.id,
        ))

    db.commit()
    return BulkResult(affected=len(host_ids), requested=len(payload.host_ids))


class BulkFollowRequest(BaseModel):
    host_ids: List[int]
    status: FollowStatus


@router.post("/bulk/follow", response_model=BulkResult, summary="Set follow status on many hosts (for the caller)")
def bulk_follow(
    payload: BulkFollowRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host_ids = _valid_host_ids(db, project.id, payload.host_ids)
    if not host_ids:
        return BulkResult(affected=0, requested=len(payload.host_ids))

    existing = {
        f.host_id: f
        for f in db.query(HostFollow)
        .filter(HostFollow.user_id == current_user.id, HostFollow.host_id.in_(host_ids))
        .all()
    }
    for h in host_ids:
        follow = existing.get(h)
        if follow:
            follow.status = payload.status
        else:
            db.add(HostFollow(host_id=h, user_id=current_user.id, status=payload.status))

    db.commit()
    return BulkResult(affected=len(host_ids), requested=len(payload.host_ids))
