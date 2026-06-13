"""
Project Management API Endpoints

CRUD for projects and project membership management.
"""

import re
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, ConfigDict, Field

from app.db.session import get_db
from app.db.models_project import Project, ProjectMembership
from app.db.models_auth import User, UserRole
from app.api.v1.endpoints.auth import get_current_user, require_role

router = APIRouter(dependencies=[Depends(get_current_user)])


# --- Schemas ---

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Project name (must be unique)")
    description: Optional[str] = None
    status: str = Field("active", description="Project status: active, in_progress, completed, archived")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    status: Optional[str] = Field(None, description="Project status: active, in_progress, completed, archived")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


_VALID_STATUSES = {"active", "in_progress", "completed", "archived"}


class ProjectResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    status: str = "active"
    is_default: bool = False
    is_archived: bool = False
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    member_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class MembershipCreate(BaseModel):
    user_id: int
    role: str = Field("viewer", description="Project role: admin, analyst, auditor, viewer")


class MembershipUpdate(BaseModel):
    role: str = Field(..., description="Project role: admin, analyst, auditor, viewer")


class MembershipResponse(BaseModel):
    id: int
    project_id: int
    user_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    message: str


_VALID_ROLES = {"admin", "analyst", "auditor", "viewer"}

_AUTH_RESPONSES = {
    401: {"description": "Not authenticated"},
}

_ADMIN_RESPONSES = {
    401: {"description": "Not authenticated"},
    403: {"description": "Insufficient permissions"},
}


def _slugify(name: str) -> str:
    """Convert project name to URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug[:100]


def _can_manage_project(
    project: Project,
    current_user: User,
    db: Session,
) -> bool:
    """Check if user can manage project settings/members."""
    if current_user.role == UserRole.ADMIN:
        return True
    membership = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project.id,
        ProjectMembership.user_id == current_user.id,
        ProjectMembership.role == "admin",
    ).first()
    return membership is not None


# --- Project CRUD ---

@router.get(
    "/",
    response_model=List[ProjectResponse],
    responses=_AUTH_RESPONSES,
    summary="List projects",
)
def list_projects(
    include_archived: bool = Query(False, description="Include archived projects"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List projects the current user has access to. Global admins see all projects."""
    query = db.query(Project)

    if not include_archived:
        query = query.filter(Project.is_archived == False)

    # Non-admins only see their projects
    if current_user.role != UserRole.ADMIN:
        user_project_ids = db.query(ProjectMembership.project_id).filter(
            ProjectMembership.user_id == current_user.id
        ).scalar_subquery()
        query = query.filter(Project.id.in_(user_project_ids))

    # Alphabetical only. The dropped tiebreaker was "is_default first",
    # part of the same removed-default-project concept the frontend now
    # auto-selects via MRU instead.
    projects = query.order_by(Project.name.asc()).all()

    # RV-11 — member counts in ONE grouped query instead of a count() per
    # project (N+1).  Empty projects simply fall back to 0 via .get().
    member_counts = dict(
        db.query(ProjectMembership.project_id, func.count(ProjectMembership.id))
        .filter(ProjectMembership.project_id.in_([p.id for p in projects]))
        .group_by(ProjectMembership.project_id)
        .all()
    ) if projects else {}

    result = []
    for p in projects:
        resp = ProjectResponse.model_validate(p)
        resp.member_count = member_counts.get(p.id, 0)
        result.append(resp)

    return result


@router.post(
    "/",
    response_model=ProjectResponse,
    status_code=201,
    responses=_AUTH_RESPONSES,
    summary="Create project",
)
def create_project(
    data: ProjectCreate,
    db: Session = Depends(get_db),
    # RV-3 — project creation is a global-admin action.  Previously this
    # only required authentication, so any user could create a project and
    # be auto-granted its admin role, bypassing the UI's admin-only policy.
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a new project. The creator is automatically added as a project admin."""
    # Check name uniqueness
    existing = db.query(Project).filter(Project.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="A project with this name already exists")

    slug = _slugify(data.name)
    # Ensure slug uniqueness
    slug_base = slug
    counter = 1
    while db.query(Project).filter(Project.slug == slug).first():
        slug = f"{slug_base}-{counter}"
        counter += 1

    if data.status and data.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(_VALID_STATUSES)}")

    project = Project(
        name=data.name,
        slug=slug,
        description=data.description,
        status=data.status or "active",
        # RV-3 — keep is_archived consistent with status at creation, so a
        # project created as "archived" doesn't linger active in selection.
        is_archived=(data.status == "archived"),
        start_date=data.start_date,
        end_date=data.end_date,
        created_by_id=current_user.id,
    )
    db.add(project)
    db.flush()

    # Add creator as project admin
    membership = ProjectMembership(
        project_id=project.id,
        user_id=current_user.id,
        role="admin",
    )
    db.add(membership)
    db.commit()

    resp = ProjectResponse.model_validate(project)
    resp.member_count = 1
    return resp


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "Project not found"}},
    summary="Get project details",
)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get project details. User must be a member or a global admin."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check access
    if current_user.role != UserRole.ADMIN:
        membership = db.query(ProjectMembership).filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == current_user.id,
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this project")

    count = db.query(func.count(ProjectMembership.id)).filter(
        ProjectMembership.project_id == project.id
    ).scalar()
    resp = ProjectResponse.model_validate(project)
    resp.member_count = count
    return resp


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Project not found"}},
    summary="Update project",
)
def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update project name, description, or archive status. Requires project admin or global admin."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not _can_manage_project(project, current_user, db):
        raise HTTPException(status_code=403, detail="Only project admins can update project settings")

    if data.name is not None:
        if data.name != project.name:
            existing = db.query(Project).filter(Project.name == data.name, Project.id != project_id).first()
            if existing:
                raise HTTPException(status_code=400, detail="A project with this name already exists")
            project.name = data.name
            project.slug = _slugify(data.name)

    if data.description is not None:
        project.description = data.description

    if data.status is not None:
        if data.status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(_VALID_STATUSES)}")
        # Refuse to archive the last remaining active project — the
        # actual invariant we care about is "the workspace always has
        # at least one project to land in", which the previous
        # is_default guard was approximating poorly.
        if data.status == "archived":
            active_count = db.query(Project).filter(
                Project.id != project.id,
                Project.is_archived == False,  # noqa: E712
            ).count()
            if active_count == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot archive the only active project — create another first.",
                )
        project.status = data.status
        project.is_archived = (data.status == "archived")

    if data.start_date is not None:
        project.start_date = data.start_date

    if data.end_date is not None:
        project.end_date = data.end_date

    db.commit()
    return ProjectResponse.model_validate(project)


@router.delete(
    "/{project_id}",
    response_model=MessageResponse,
    responses={
        **_ADMIN_RESPONSES,
        404: {"description": "Project not found"},
    },
    summary="Delete project (admin)",
)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete a project and all its data. Requires global admin.
    Refuses to delete the last remaining project so the workspace is
    never empty."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Real invariant: the workspace must always have at least one
    # project for users to land in. Previous "is_default" guard was a
    # weaker approximation of this.
    remaining = db.query(Project).filter(Project.id != project.id).count()
    if remaining == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the only project — create another first.",
        )

    # R6: capture the project's attachment note-ids BEFORE the cascade deletes
    # the NoteAttachment rows, then drop the on-disk files after the commit.
    from app.db import models as _models
    from app.services.note_attachment_service import purge_note_files
    _attachment_note_ids = {
        r[0]
        for r in db.query(_models.NoteAttachment.annotation_id)
        .filter(_models.NoteAttachment.project_id == project.id)
        .all()
    }
    project_name = project.name
    db.delete(project)
    db.commit()
    for _nid in _attachment_note_ids:
        purge_note_files(_nid)
    return {"message": f"Project '{project_name}' deleted"}


# --- Membership Management ---

@router.get(
    "/{project_id}/members",
    response_model=List[MembershipResponse],
    responses={**_AUTH_RESPONSES, 404: {"description": "Project not found"}},
    summary="List project members",
)
def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all members of a project. User must be a member or global admin."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if current_user.role != UserRole.ADMIN:
        membership = db.query(ProjectMembership).filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == current_user.id,
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this project")

    memberships = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id
    ).all()

    # SOC-P3 — batch the user lookup instead of one query per membership.
    user_ids = [m.user_id for m in memberships]
    users_by_id = {}
    if user_ids:
        users_by_id = {
            u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
        }

    result = []
    for m in memberships:
        user = users_by_id.get(m.user_id)
        resp = MembershipResponse(
            id=m.id,
            project_id=m.project_id,
            user_id=m.user_id,
            username=user.username if user else None,
            full_name=user.full_name if user else None,
            role=m.role,
            created_at=m.created_at,
        )
        result.append(resp)

    return result


@router.post(
    "/{project_id}/members",
    response_model=MembershipResponse,
    status_code=201,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Project or user not found"}},
    summary="Add project member",
)
def add_member(
    project_id: int,
    data: MembershipCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a user to a project. Requires project admin or global admin."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not _can_manage_project(project, current_user, db):
        raise HTTPException(status_code=403, detail="Only project admins can manage members")

    if data.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(_VALID_ROLES)}")

    user = db.query(User).filter(User.id == data.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == data.user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User is already a member of this project")

    membership = ProjectMembership(
        project_id=project_id,
        user_id=data.user_id,
        role=data.role,
    )
    db.add(membership)
    db.commit()

    return MembershipResponse(
        id=membership.id,
        project_id=membership.project_id,
        user_id=membership.user_id,
        username=user.username,
        full_name=user.full_name,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.put(
    "/{project_id}/members/{user_id}",
    response_model=MembershipResponse,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Membership not found"}},
    summary="Update member role",
)
def update_member(
    project_id: int,
    user_id: int,
    data: MembershipUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a member's project role. Requires project admin or global admin."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not _can_manage_project(project, current_user, db):
        raise HTTPException(status_code=403, detail="Only project admins can manage members")

    if data.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(_VALID_ROLES)}")

    membership = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="User is not a member of this project")

    membership.role = data.role
    db.commit()

    user = db.query(User).filter(User.id == user_id).first()
    return MembershipResponse(
        id=membership.id,
        project_id=membership.project_id,
        user_id=membership.user_id,
        username=user.username if user else None,
        full_name=user.full_name if user else None,
        role=membership.role,
        created_at=membership.created_at,
    )


@router.delete(
    "/{project_id}/members/{user_id}",
    response_model=MessageResponse,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Membership not found"}},
    summary="Remove project member",
)
def remove_member(
    project_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a user from a project. Requires project admin or global admin."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not _can_manage_project(project, current_user, db):
        raise HTTPException(status_code=403, detail="Only project admins can manage members")

    membership = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="User is not a member of this project")

    # Prevent removing the last admin
    if membership.role == "admin":
        admin_count = db.query(func.count(ProjectMembership.id)).filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.role == "admin",
        ).scalar()
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last project admin")

    db.delete(membership)
    db.commit()
    return {"message": "Member removed from project"}
