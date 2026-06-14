"""Host follow/unfollow endpoints."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models import HostFollow
from app.db.models_auth import User, UserRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.schemas.schemas import HostFollowInfo, HostFollowUpdate
from app.services.host_follow_service import HostFollowService
from app.services.notification_service import NotificationService
from app.services.webhook_dispatcher import safe_dispatch
# CR4-2 — serializer moved to the service layer (was defined here and
# imported back by host_serialization, a service -> router dependency).
from app.services.host_serialization import _serialize_follow

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.post(
    "/{host_id:int}/follow",
    response_model=HostFollowInfo,
    summary="Follow a host (set review status)",
)
def follow_host(
    host_id: int,
    payload: HostFollowUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)
    follow = follow_service.set_follow_status(
        host_id, current_user.id, payload.status,
        review_conclusion=payload.review_conclusion,
        review_summary=payload.review_summary,
    )
    return _serialize_follow(follow)


@router.delete(
    "/{host_id:int}/follow",
    status_code=204,
    summary="Unfollow a host",
)
def unfollow_host(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)
    follow_service.unfollow(host_id, current_user.id)
    return Response(status_code=204)


@router.post(
    "/{host_id:int}/view",
    status_code=204,
    summary="Record a host view (updates last_viewed_at)",
)
def record_host_view(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Record that the current user viewed this host (updates last_viewed_at).

    No-op for hosts the user has not explicitly followed — see
    `HostFollowService.record_view` for the rationale.
    """
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    HostFollowService(db).record_view(host_id, current_user.id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Followers list — exposes "who else is reviewing this host" so the host
# detail page can show team coordination context.
# ---------------------------------------------------------------------------

class HostFollowerEntry(BaseModel):
    """One row of the host followers response — a single user who is
    actively following this host."""
    user_id: int
    username: str
    full_name: Optional[str] = None
    status: str  # "watching" | "in_review" | "reviewed"
    since: datetime  # follow record's updated_at if present, else created_at


class HostFollowersResponse(BaseModel):
    followers: List[HostFollowerEntry]


@router.get(
    "/{host_id:int}/followers",
    response_model=HostFollowersResponse,
    summary="List other users following this host",
)
def list_host_followers(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Return all users currently following this host (any status).

    Used by the host detail page to surface team coordination context
    ("Also reviewing this host: alice, bob").  The current user is
    intentionally **excluded** from the response — the page already
    shows the user's own follow state in its own control, so listing
    yourself again would be noise.

    Returned in priority order: in_review first, then watching, then
    reviewed.  Each group sorted by most recent update.
    """
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == project.id)
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rows = (
        db.query(HostFollow, User)
        .join(User, HostFollow.user_id == User.id)
        .filter(
            HostFollow.host_id == host_id,
            HostFollow.user_id != current_user.id,
        )
        .all()
    )

    # Sort in Python rather than SQL — the row count is bounded by
    # team size and the priority logic is simpler to read here than
    # as a CASE expression.
    STATUS_PRIORITY = {"in_review": 0, "watching": 1, "reviewed": 2}

    def _sort_key(item):
        follow, _user = item
        status_value = follow.status.value if hasattr(follow.status, "value") else str(follow.status)
        priority = STATUS_PRIORITY.get(status_value, 3)
        # Use updated_at when present so the most recently touched
        # follow lands at the top of its group; created_at as fallback.
        ts = follow.updated_at or follow.created_at
        # Negate the timestamp via a tuple so newer comes first under
        # an ascending sort.
        return (priority, -(ts.timestamp() if ts else 0))

    rows.sort(key=_sort_key)

    followers = [
        HostFollowerEntry(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            status=follow.status.value if hasattr(follow.status, "value") else str(follow.status),
            since=follow.updated_at or follow.created_at,
        )
        for follow, user in rows
    ]
    return HostFollowersResponse(followers=followers)


# ---------------------------------------------------------------------------
# Host assignment / ownership (v2.71.0).  Assignment is a follow row for
# the assignee with ``assigned_at`` set; assigning bumps status to In
# Review so the host enters the assignee's My Queue.
# ---------------------------------------------------------------------------

class HostAssignRequest(BaseModel):
    assignee_user_id: int


class HostAssignmentInfo(BaseModel):
    host_id: int
    user_id: int
    assigned_by_id: Optional[int] = None
    assigned_at: Optional[datetime] = None
    status: str


def _status_str(follow: HostFollow) -> str:
    return follow.status.value if hasattr(follow.status, "value") else str(follow.status)


@router.post(
    "/{host_id:int}/assign",
    response_model=HostAssignmentInfo,
    summary="Assign a host to a project member",
    # Assigning a host to another member mutates shared workflow state and
    # fires notifications/webhooks — analyst+ only, not every member.
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def assign_host(
    host_id: int,
    payload: HostAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(
        models.Host.id == host_id, models.Host.project_id == project.id
    ).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    assignee = db.query(User).filter(
        User.id == payload.assignee_user_id, User.is_active.is_(True)
    ).first()
    if not assignee:
        raise HTTPException(status_code=404, detail="Assignee not found")

    # The assignee must be able to see this project — a global admin can,
    # otherwise a ProjectMembership row is required.  Assigning a host to
    # someone with no access would create a dead queue entry and leak the
    # host label into their notification feed.
    if assignee.role != UserRole.ADMIN:
        is_member = db.query(ProjectMembership).filter(
            ProjectMembership.project_id == project.id,
            ProjectMembership.user_id == assignee.id,
        ).first()
        if not is_member:
            raise HTTPException(status_code=400, detail="Assignee is not a member of this project")

    follow = HostFollowService(db).assign_host(host_id, assignee.id, current_user.id)

    # Notification is best-effort — a failure here must not roll back the
    # assignment itself (which already committed in the service).
    try:
        NotificationService(db).notify_host_assignment(assignee, host, project, current_user)
        db.commit()
    except Exception:
        db.rollback()

    # Outbound webhook (post-commit, fire-and-forget).
    safe_dispatch(
        db,
        project_id=project.id,
        event="host_assigned",
        title=f"{host.hostname or host.ip_address} assigned to {assignee.full_name or assignee.username}",
        body=f"Assigned by @{current_user.username}",
        context={"host_id": host_id, "assignee_user_id": assignee.id},
    )

    return HostAssignmentInfo(
        host_id=host_id,
        user_id=assignee.id,
        assigned_by_id=follow.assigned_by_id,
        assigned_at=follow.assigned_at,
        status=_status_str(follow),
    )


@router.delete(
    "/{host_id:int}/assign",
    status_code=204,
    summary="Unassign a host from a user (keeps their follow row)",
    # Shared-state mutation (see assign) — analyst+ only.
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def unassign_host(
    host_id: int,
    user_id: int = Query(..., description="The assignee to unassign"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = db.query(models.Host).filter(
        models.Host.id == host_id, models.Host.project_id == project.id
    ).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    HostFollowService(db).unassign_host(host_id, user_id)
    return Response(status_code=204)
