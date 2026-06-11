"""
Audit Logging API Endpoints

Endpoints for audit trail logging and retrieval.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, Request, Query, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.core.security import log_audit_event

router = APIRouter(dependencies=[Depends(get_current_user)])


# --- Schemas ---

class AuditLogRequest(BaseModel):
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class AuditLogResponse(BaseModel):
    message: str
    audit_id: Optional[int] = None


class AuditLogEntry(BaseModel):
    id: int
    user_id: Optional[int] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None
    error_message: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: Optional[datetime] = None


class AuditLogListResponse(BaseModel):
    logs: List[AuditLogEntry]
    total: int
    skip: int
    limit: int


class ActionCount(BaseModel):
    action: str
    count: int


class UserCount(BaseModel):
    user_id: Optional[int] = None
    count: int


class AuditStatsResponse(BaseModel):
    total_logs: int = 0
    successful_logs: int = 0
    failed_logs: int = 0
    recent_logs_24h: int = 0
    top_actions: List[ActionCount]
    top_users: List[UserCount]


_AUTH_RESPONSES = {
    401: {"description": "Not authenticated"},
}

_ADMIN_RESPONSES = {
    401: {"description": "Not authenticated"},
    403: {"description": "Insufficient permissions — admin role required"},
}


def get_client_info(request: Request) -> Dict[str, Optional[str]]:
    """Extract client information from request"""
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent")
    }


# Actions a client is permitted to self-report via /audit/log.  Deliberately
# narrow + non-privileged: the authoritative security events (login_success,
# password_changed, user_*, role_*, …) are written backend-side at the
# operation boundary and must never originate from a client.  login_success was
# removed from the frontend (R7); these remain as plausible UI telemetry.
_CLIENT_ALLOWED_ACTIONS = {
    "logout",
    "session_expired",
    "client_error",
}


@router.post(
    "/log",
    response_model=AuditLogResponse,
    responses=_AUTH_RESPONSES,
    summary="Create audit log entry",
)
def create_audit_log(
    audit_data: AuditLogRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create an audit log entry for the current user's action.

    Client-submitted: an authenticated client could previously inject ANY
    ``action`` string into the admin-facing audit trail (code-review R7).  We
    now (a) restrict client events to an allowlist of genuinely client-side
    telemetry — privileged/backend action names are rejected — and (b) stamp
    ``source=client`` so admins can distinguish trusted backend events (written
    at the operation boundary) from client-reported ones.
    """
    if audit_data.action not in _CLIENT_ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This action is not permitted for client-submitted audit events.",
        )
    client_info = get_client_info(request)

    # No inner try/except — the global handler in main.py:359-382 logs
    # the traceback and returns the standard ``{"detail": "Internal
    # server error", "error_class": ...}`` shape.  The old wrapper
    # leaked raw exception text into the response body (ORM/driver
    # internals), which both contradicted the global handler's
    # sanitisation and made the failure harder to diagnose because
    # ``str(e)`` rarely matches the logged traceback.
    audit_id = log_audit_event(
        db=db,
        user_id=current_user.id,
        action=audit_data.action,
        resource_type=audit_data.resource_type,
        resource_id=audit_data.resource_id,
        # Mark the row as client-reported so it's never mistaken for a
        # backend-authoritative event during audit review.
        details={**(audit_data.details or {}), "source": "client"},
        **client_info
    )

    return AuditLogResponse(
        message="Audit log created successfully",
        audit_id=audit_id
    )


@router.get(
    "/logs",
    response_model=AuditLogListResponse,
    responses=_ADMIN_RESPONSES,
    summary="Get audit logs (admin)",
)
def get_audit_logs(
    # v2.86.4 — skip lower-bound enforced; limit was already capped.
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    action: Optional[str] = Query(None, description="Filter by action type"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Get audit logs with optional filters. Requires admin role."""
    # Import here to avoid circular imports
    from app.db.models_auth import AuditLog

    query = db.query(AuditLog)

    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)

    # Order by most recent first
    query = query.order_by(AuditLog.timestamp.desc())

    total = query.count()

    # Apply pagination
    logs = query.offset(skip).limit(limit).all()

    return {
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "details": log.details,
                "success": log.success,
                "error_message": log.error_message,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "created_at": log.timestamp
            }
            for log in logs
        ],
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.get(
    "/stats",
    response_model=AuditStatsResponse,
    responses=_ADMIN_RESPONSES,
    summary="Audit statistics (admin)",
)
def get_audit_stats(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Get audit statistics including event counts and top actions/users.
    Requires admin role."""
    # Import here to avoid circular imports
    from app.db.models_auth import AuditLog
    from sqlalchemy import func

    # Get basic stats
    total_logs = db.query(AuditLog).count()
    successful_logs = db.query(AuditLog).filter(AuditLog.success == True).count()
    failed_logs = db.query(AuditLog).filter(AuditLog.success == False).count()

    # Get recent activity (rolling 24-hour window).
    # AuditLog.timestamp is DateTime(timezone=True) so the comparison
    # operand must be tz-aware to match — same class of bug v2.41.0
    # fixed in auth.py.
    from datetime import timedelta
    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_logs = db.query(AuditLog).filter(
        AuditLog.timestamp >= twenty_four_hours_ago
    ).count()

    # Get top actions
    top_actions = db.query(
        AuditLog.action,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.action).order_by(
        func.count(AuditLog.id).desc()
    ).limit(10).all()

    # Get top users
    top_users = db.query(
        AuditLog.user_id,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.user_id).order_by(
        func.count(AuditLog.id).desc()
    ).limit(10).all()

    return {
        "total_logs": total_logs,
        "successful_logs": successful_logs,
        "failed_logs": failed_logs,
        "recent_logs_24h": recent_logs,
        "top_actions": [
            {"action": action, "count": count}
            for action, count in top_actions
        ],
        "top_users": [
            {"user_id": user_id, "count": count}
            for user_id, count in top_users
        ]
    }
