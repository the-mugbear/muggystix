"""Host tag management (v2.71.0).

Project-scoped tags ("prod", "DMZ", "owned", …) and the host<->tag
assignments.  Mounted under the ``/hosts`` prefix alongside host-follow
and host-notes; tag-definition routes live at ``/tags`` (a static
segment, so it never collides with ``/{host_id:int}/...``).

Reading tags is open to any project member; mutating them (create / rename /
delete a tag, assign / remove on a host) requires analyst+ — viewer and auditor
are read-only roles, so they must not be able to alter shared project state.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.session import get_db
from app.db import models
from app.db.models import HostTag, HostTagAssignment
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role

router = APIRouter(dependencies=[Depends(get_current_user)])


class TagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    color: Optional[str] = Field(None, max_length=20)


class TagInfo(BaseModel):
    id: int
    name: str
    color: Optional[str] = None
    host_count: int = 0

    model_config = ConfigDict(from_attributes=True)


def _tag_or_404(db: Session, project_id: int, tag_id: int) -> HostTag:
    tag = (
        db.query(HostTag)
        .filter(HostTag.id == tag_id, HostTag.project_id == project_id)
        .first()
    )
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


@router.get("/tags", response_model=List[TagInfo], summary="List project tags with host counts")
def list_tags(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    rows = (
        db.query(HostTag, func.count(HostTagAssignment.id))
        .outerjoin(HostTagAssignment, HostTagAssignment.tag_id == HostTag.id)
        .filter(HostTag.project_id == project.id)
        .group_by(HostTag.id)
        .order_by(HostTag.name)
        .all()
    )
    return [
        TagInfo(id=tag.id, name=tag.name, color=tag.color, host_count=count or 0)
        for tag, count in rows
    ]


@router.patch(
    "/tags/{tag_id:int}", response_model=TagInfo, summary="Rename or recolor a tag",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def update_tag(
    tag_id: int,
    payload: TagUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    tag = _tag_or_404(db, project.id, tag_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Tag name cannot be empty")
        tag.name = name
    if payload.color is not None:
        tag.color = payload.color or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A tag with that name already exists in this project")
    db.refresh(tag)
    count = (
        db.query(func.count(HostTagAssignment.id))
        .filter(HostTagAssignment.tag_id == tag.id)
        .scalar()
    ) or 0
    return TagInfo(id=tag.id, name=tag.name, color=tag.color, host_count=count)


@router.delete(
    "/tags/{tag_id:int}", status_code=204, summary="Delete a tag (removes all its assignments)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def delete_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    tag = _tag_or_404(db, project.id, tag_id)
    db.delete(tag)  # assignments cascade via FK ondelete=CASCADE + ORM cascade
    db.commit()
    return Response(status_code=204)
