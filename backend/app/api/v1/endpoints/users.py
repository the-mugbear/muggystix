"""
User Management API Endpoints

Endpoints for admin user management including listing users, updating profiles,
changing roles, activating/deactivating accounts.
"""

from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field

from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.core.security import (
    get_password_hash,
    validate_password_strength,
    log_audit_event,
    check_permissions
)
from app.api.v1.endpoints.auth import get_current_user, require_role, get_client_info


# --- Shared error responses for role-gated endpoints ---
_AUTH_RESPONSES = {
    401: {"description": "Not authenticated — missing or invalid JWT token"},
    403: {"description": "Insufficient permissions — admin role required"},
}


router = APIRouter(dependencies=[Depends(get_current_user)])


# Pydantic models
class UserListItem(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    is_active: bool
    last_login: Optional[datetime] = None
    created_at: datetime
    created_by_id: Optional[int] = None
    # Whether the user has enrolled in 2FA — surfaced so admins can see the
    # estate's 2FA coverage and reset a locked-out user.
    totp_enabled: bool = False


class UserDirectoryEntry(BaseModel):
    """Minimal user row for member-picker dropdowns.

    Intentionally omits email, role, last_login, created_at, etc. — the
    caller (usually a project admin adding someone to their project)
    only needs id + a human label.  Kept separate from ``UserListItem``
    so this remains open to any authenticated user without leaking the
    admin-only fields.
    """
    id: int
    username: str
    full_name: Optional[str] = None


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = Field(None, description="Global role — 'admin' or 'member' (v2.46.0)")
    is_active: Optional[bool] = None


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=12, description="Must contain uppercase, lowercase, digit, and special character")


class UserProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


class UserProfileResponse(BaseModel):
    """Response for self-service profile update."""
    message: str


class MyProjectMembership(BaseModel):
    """A row in the user's own project-association list.

    Surfaced on the Profile page so the operator can see at a glance
    which projects they belong to and what role they hold in each.
    The user's *global* role (admin/analyst/...) lives on User and
    governs cross-project capabilities; the per-project role here
    governs membership-scoped capabilities and may differ.
    """
    project_id: int
    project_name: str
    project_slug: str
    project_status: str
    project_is_default: bool = False
    project_is_archived: bool = False
    role: str
    joined_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ #
#  Self-service profile endpoint — registered BEFORE /{user_id}      #
#  routes so that FastAPI does not try to parse "profile" as an int.  #
# ------------------------------------------------------------------ #

@router.put(
    "/profile",
    response_model=UserProfileResponse,
    responses={401: {"description": "Not authenticated"}},
    summary="Update own profile",
)
def update_own_profile(
    profile_data: UserProfileUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the authenticated user's own profile. Any role can use this endpoint."""
    client_info = get_client_info(request)

    changes = {}

    if profile_data.full_name is not None:
        changes["full_name"] = {"old": current_user.full_name, "new": profile_data.full_name}
        current_user.full_name = profile_data.full_name

    db.commit()

    # Log profile update
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="profile_updated",
        details={"changes": changes},
        **client_info
    )

    return {"message": "Profile updated successfully"}


# ------------------------------------------------------------------ #
#  Self-service: list the projects the current user belongs to        #
#  — also registered BEFORE /{user_id} so "profile" isn't parsed as   #
#  an int.                                                            #
# ------------------------------------------------------------------ #

@router.get(
    "/profile/projects",
    response_model=List[MyProjectMembership],
    responses={401: {"description": "Not authenticated"}},
    summary="List the current user's project associations",
)
def my_project_memberships(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return every project the authenticated user is a member of,
    with the user's per-project role and the project's status.

    Global admins implicitly have access to every project but may not
    have an explicit ``ProjectMembership`` row in each; we surface those
    too with ``role = "admin"`` so the Profile page reflects the user's
    actual reach. Non-admins see only their explicit memberships.
    """
    memberships = (
        db.query(ProjectMembership, Project)
        .join(Project, Project.id == ProjectMembership.project_id)
        .filter(ProjectMembership.user_id == current_user.id)
        .all()
    )
    explicit_rows = [
        MyProjectMembership(
            project_id=p.id,
            project_name=p.name,
            project_slug=p.slug,
            project_status=p.status,
            project_is_default=p.is_default,
            project_is_archived=p.is_archived,
            role=m.role,
            joined_at=m.created_at,
        )
        for m, p in memberships
    ]

    # Global admins also see all projects they aren't explicitly listed
    # on (membership row may not exist for every project they manage).
    if current_user.role == UserRole.ADMIN:
        explicit_ids = {row.project_id for row in explicit_rows}
        implicit_projects = (
            db.query(Project)
            .filter(~Project.id.in_(explicit_ids) if explicit_ids else True)
            .all()
        )
        for p in implicit_projects:
            explicit_rows.append(
                MyProjectMembership(
                    project_id=p.id,
                    project_name=p.name,
                    project_slug=p.slug,
                    project_status=p.status,
                    project_is_default=p.is_default,
                    project_is_archived=p.is_archived,
                    # Global admin role surfaces as "admin" per project
                    # so the Profile UI can render a consistent badge.
                    role="admin",
                    joined_at=None,
                )
            )

    # Alphabetical by name. The dropped tiebreaker was "default project
    # first" — that concept was removed (engagements are independent,
    # not hierarchical; the auto-set default was whichever project
    # happened to be created first, never a meaningful preference).
    explicit_rows.sort(key=lambda r: r.project_name.lower())
    return explicit_rows


# ------------------------------------------------------------------ #
#  Directory — open to any authenticated user                         #
# ------------------------------------------------------------------ #

@router.get(
    "/directory",
    response_model=List[UserDirectoryEntry],
    summary="List users for member pickers",
)
def user_directory(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a minimal (id, username, full_name) list of active users.

    Used by the project-settings Add Member dialog so a project admin
    (who may not hold the global admin role) can still pick from
    existing users instead of typing a username.  Inactive users are
    omitted — you can't grant membership to a deactivated account.
    """
    users = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.username)
        .all()
    )
    return [
        UserDirectoryEntry(id=u.id, username=u.username, full_name=u.full_name)
        for u in users
    ]


# ------------------------------------------------------------------ #
#  Admin endpoints — all require admin role                           #
# ------------------------------------------------------------------ #

@router.get(
    "/",
    response_model=List[UserListItem],
    responses=_AUTH_RESPONSES,
    summary="List all users (admin)",
)
def list_users(
    # v2.86.4 — pagination caps added.
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """List all users. Requires admin role."""
    users = db.query(User).offset(skip).limit(limit).all()

    return [
        UserListItem(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            last_login=user.last_login,
            created_at=user.created_at,
            created_by_id=user.created_by_id,
            totp_enabled=bool(user.totp_enabled),
        )
        for user in users
    ]


@router.get(
    "/{user_id}",
    response_model=UserListItem,
    responses={**_AUTH_RESPONSES, 404: {"description": "User not found"}},
    summary="Get user details (admin)",
)
def get_user(
    user_id: int,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Get specific user details. Requires admin role."""
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return UserListItem(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        last_login=user.last_login,
        created_at=user.created_at,
        created_by_id=user.created_by_id,
        totp_enabled=bool(user.totp_enabled),
    )


@router.get(
    "/{user_id}/memberships",
    response_model=List[MyProjectMembership],
    responses={**_AUTH_RESPONSES, 404: {"description": "User not found"}},
    summary="List a specific user's project memberships (admin)",
)
def get_user_memberships(
    user_id: int,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Return every project the target user belongs to, with their
    per-project role and the project's status.  v2.59.0 — admin
    surface for the "Manage memberships" affordance on the system-
    settings users list.  Mirrors the self-service `/profile/projects`
    response shape so the frontend can reuse the same row component.

    Global admins on the target account are surfaced consistently
    with the self-service flow: every unarchived project they DON'T
    have an explicit membership row in is rolled up with `role='admin'`
    so the panel shows the user's actual reach, not just the explicit
    rows.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    memberships = (
        db.query(ProjectMembership, Project)
        .join(Project, Project.id == ProjectMembership.project_id)
        .filter(ProjectMembership.user_id == user_id)
        .all()
    )
    explicit_rows = [
        MyProjectMembership(
            project_id=p.id,
            project_name=p.name,
            project_slug=p.slug,
            project_status=p.status,
            project_is_default=p.is_default,
            project_is_archived=p.is_archived,
            role=m.role,
            joined_at=m.created_at,
        )
        for m, p in memberships
    ]

    if user.role == UserRole.ADMIN:
        explicit_ids = {row.project_id for row in explicit_rows}
        implicit_projects = (
            db.query(Project)
            .filter(~Project.id.in_(explicit_ids) if explicit_ids else True)
            .all()
        )
        for p in implicit_projects:
            explicit_rows.append(
                MyProjectMembership(
                    project_id=p.id,
                    project_name=p.name,
                    project_slug=p.slug,
                    project_status=p.status,
                    project_is_default=p.is_default,
                    project_is_archived=p.is_archived,
                    role="admin",
                    joined_at=None,
                )
            )

    explicit_rows.sort(key=lambda r: r.project_name.lower())
    return explicit_rows


@router.put(
    "/{user_id}",
    response_model=UserListItem,
    responses={**_AUTH_RESPONSES, 404: {"description": "User not found"}},
    summary="Update user (admin)",
)
def update_user(
    user_id: int,
    update_data: UserUpdateRequest,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Update user details (role, name, active status). Requires admin role."""
    client_info = get_client_info(request)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Prevent admin from demoting themselves
    if user_id == current_user.id and update_data.role and update_data.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own admin role"
        )

    # Prevent admin from deactivating themselves
    if user_id == current_user.id and update_data.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )

    changes = {}

    # Update fields if provided
    if update_data.full_name is not None:
        changes["full_name"] = {"old": user.full_name, "new": update_data.full_name}
        user.full_name = update_data.full_name

    if update_data.role is not None:
        # v2.46.0 — global role is binary.  Per-project capability
        # (analyst/auditor/viewer) is set on project membership, not here.
        if update_data.role not in (UserRole.ADMIN.value, UserRole.MEMBER.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role — global role must be 'admin' or 'member'",
            )
        changes["role"] = {"old": user.role, "new": update_data.role}
        user.role = update_data.role

    if update_data.is_active is not None:
        changes["is_active"] = {"old": user.is_active, "new": update_data.is_active}
        user.is_active = update_data.is_active

    db.commit()

    # Log the changes
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="user_updated",
        resource_type="user",
        resource_id=str(user_id),
        details={"changes": changes, "target_user": user.username},
        **client_info
    )

    return UserListItem(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        last_login=user.last_login,
        created_at=user.created_at,
        created_by_id=user.created_by_id,
        totp_enabled=bool(user.totp_enabled),
    )


@router.post(
    "/{user_id}/reset-password",
    response_model=MessageResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "User not found"}},
    summary="Reset user password (admin)",
)
def admin_reset_password(
    user_id: int,
    password_data: AdminPasswordResetRequest,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Reset a user's password. Requires admin role."""
    client_info = get_client_info(request)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Validate password strength
    password_validation = validate_password_strength(password_data.new_password)
    if not password_validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password validation failed: {', '.join(password_validation['errors'])}"
        )

    # Update password.  Force the target user to set their own password on
    # next login — an admin-chosen password is a transit credential, never a
    # standing one (mirrors the first-boot admin's forced-change behavior).
    user.hashed_password = get_password_hash(password_data.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    user.must_change_password = True

    db.commit()

    # Log password reset
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="admin_password_reset",
        resource_type="user",
        resource_id=str(user_id),
        details={"target_user": user.username},
        **client_info
    )

    return {"message": f"Password reset for user {user.username}"}


@router.post(
    "/{user_id}/reset-2fa",
    response_model=MessageResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "User not found"}},
    summary="Reset (disable) a user's 2FA enrollment (admin)",
)
def admin_reset_two_factor(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Clear a user's TOTP 2FA — the recovery path for a lost/replaced
    authenticator.  With REQUIRE_2FA on, the user is forced to re-enroll on
    their next login; otherwise 2FA simply becomes opt-in again for them."""
    from app.db.models_auth import UserRecoveryCode

    client_info = get_client_info(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.totp_confirmed_at = None
    db.query(UserRecoveryCode).filter(UserRecoveryCode.user_id == user.id).delete()
    db.commit()

    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="admin_2fa_reset",
        resource_type="user",
        resource_id=str(user_id),
        details={"target_user": user.username},
        **client_info,
    )
    return {"message": f"2FA reset for user {user.username}"}


@router.delete(
    "/{user_id}",
    response_model=MessageResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "User not found"}},
    summary="Delete user (admin)",
)
def delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Delete a user account. Requires admin role. Cannot delete your own account."""
    client_info = get_client_info(request)

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    username = user.username  # Store before deletion

    db.delete(user)
    db.commit()

    # Log user deletion
    log_audit_event(
        db=db,
        user_id=current_user.id,
        action="user_deleted",
        resource_type="user",
        resource_id=str(user_id),
        details={"deleted_username": username},
        **client_info
    )

    return {"message": f"User {username} deleted successfully"}
