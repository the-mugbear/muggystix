"""
Notification API Endpoints

In-app notification center for @mentions, status changes, and project assignments.
Notifications span projects — they're user-scoped, not project-scoped.
"""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field

from app.db.session import get_db
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.services.notification_service import NotificationService

router = APIRouter(dependencies=[Depends(get_current_user)])


# --- Schemas ---

class NotificationResponse(BaseModel):
    id: int
    type: str
    title: str
    body: Optional[str] = None
    project_id: Optional[int] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    host_id: Optional[int] = None
    actor_id: Optional[int] = None
    is_read: bool = False
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total_unread: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class MarkReadRequest(BaseModel):
    notification_ids: List[int] = Field(..., min_length=1, max_length=100)


class MarkReadResponse(BaseModel):
    marked_read: int


# --- Endpoints ---

@router.get(
    "/",
    response_model=NotificationListResponse,
    summary="List notifications",
)
def list_notifications(
    unread_only: bool = Query(False, description="Only return unread notifications"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List notifications for the current user, ordered by most recent first."""
    svc = NotificationService(db)
    notifications = svc.get_notifications(
        user_id=current_user.id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    unread_count = svc.get_unread_count(current_user.id)
    return {
        "notifications": notifications,
        "total_unread": unread_count,
    }


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get unread notification count",
)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fast endpoint for notification badge — returns unread count only."""
    svc = NotificationService(db)
    return {"unread_count": svc.get_unread_count(current_user.id)}


@router.post(
    "/mark-read",
    response_model=MarkReadResponse,
    summary="Mark notifications as read",
)
def mark_notifications_read(
    data: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark specific notification IDs as read."""
    svc = NotificationService(db)
    count = svc.mark_read(data.notification_ids, current_user.id)
    db.commit()
    return {"marked_read": count}


@router.post(
    "/mark-all-read",
    response_model=MarkReadResponse,
    summary="Mark all notifications as read",
)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all unread notifications as read for the current user."""
    svc = NotificationService(db)
    count = svc.mark_all_read(current_user.id)
    db.commit()
    return {"marked_read": count}
