"""Host follow/unfollow endpoints."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models import HostFollow
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole
from app.schemas.schemas import HostFollowInfo, HostFollowUpdate
from app.services.host_follow_service import HostFollowService
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


