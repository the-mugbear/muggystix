"""Host tag management (v2.71.0).

Project-scoped tags ("prod", "DMZ", "owned", …) and the host<->tag
assignments.  Mounted under the ``/hosts`` prefix alongside host-follow
and host-notes; tag-definition routes live at ``/tags`` (a static
segment, so it never collides with ``/{host_id:int}/...``).

Any authenticated project member may manage tags — ``get_current_project``
already enforces membership, matching the low-stakes follow/notes
surfaces rather than an admin gate.
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
from app.db.models_project import Project
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project

router = APIRouter(dependencies=[Depends(get_current_user)])


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: Optional[str] = Field(None, max_length=20)


class TagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    color: Optional[str] = Field(None, max_length=20)


class TagInfo(BaseModel):
    id: int
    name: str
    color: Optional[str] = None
    host_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class HostTagsUpdate(BaseModel):
    """Assign tags to a host: existing tags by id and/or create-and-assign
    by name (case-insensitive de-dupe against existing project tags)."""
    tag_ids: List[int] = Field(default_factory=list)
    names: List[str] = Field(default_factory=list)


def _tag_or_404(db: Session, project_id: int, tag_id: int) -> HostTag:
    tag = (
        db.query(HostTag)
        .filter(HostTag.id == tag_id, HostTag.project_id == project_id)
        .first()
    )
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


def _host_or_404(db: Session, project_id: int, host_id: int) -> models.Host:
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == project_id)
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    return host


def _serialize_host_tags(db: Session, host_id: int) -> List[TagInfo]:
    """The host's current tags (no host_count — that's a list-level stat)."""
    rows = (
        db.query(HostTag)
        .join(HostTagAssignment, HostTagAssignment.tag_id == HostTag.id)
        .filter(HostTagAssignment.host_id == host_id)
        .order_by(HostTag.name)
        .all()
    )
    return [TagInfo(id=t.id, name=t.name, color=t.color) for t in rows]


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


@router.post("/tags", response_model=TagInfo, status_code=201, summary="Create a project tag")
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tag name cannot be empty")
    tag = HostTag(
        project_id=project.id,
        name=name,
        color=payload.color,
        created_by_id=current_user.id,
    )
    db.add(tag)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"A tag named '{name}' already exists in this project")
    db.refresh(tag)
    return TagInfo(id=tag.id, name=tag.name, color=tag.color, host_count=0)


@router.patch("/tags/{tag_id:int}", response_model=TagInfo, summary="Rename or recolor a tag")
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


@router.delete("/tags/{tag_id:int}", status_code=204, summary="Delete a tag (removes all its assignments)")
def delete_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    tag = _tag_or_404(db, project.id, tag_id)
    db.delete(tag)  # assignments cascade via FK ondelete=CASCADE + ORM cascade
    db.commit()
    return Response(status_code=204)


@router.post(
    "/{host_id:int}/tags",
    response_model=List[TagInfo],
    summary="Assign tags to a host (by id and/or create-by-name)",
)
def assign_host_tags(
    host_id: int,
    payload: HostTagsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    _host_or_404(db, project.id, host_id)

    # Resolve the target tag ids: validate supplied ids belong to the
    # project, and create-or-find any names.
    target_tag_ids: set[int] = set()
    for tid in payload.tag_ids:
        _tag_or_404(db, project.id, tid)  # 404 on a foreign/unknown id
        target_tag_ids.add(tid)

    for raw_name in payload.names:
        name = (raw_name or "").strip()
        if not name:
            continue
        existing = (
            db.query(HostTag)
            .filter(HostTag.project_id == project.id, func.lower(HostTag.name) == name.lower())
            .first()
        )
        if existing:
            target_tag_ids.add(existing.id)
        else:
            tag = HostTag(project_id=project.id, name=name, created_by_id=current_user.id)
            db.add(tag)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                existing = (
                    db.query(HostTag)
                    .filter(HostTag.project_id == project.id, func.lower(HostTag.name) == name.lower())
                    .first()
                )
                if existing:
                    target_tag_ids.add(existing.id)
                continue
            target_tag_ids.add(tag.id)

    # Skip tags already on the host; insert the rest.
    already = {
        row[0]
        for row in db.query(HostTagAssignment.tag_id)
        .filter(HostTagAssignment.host_id == host_id, HostTagAssignment.tag_id.in_(target_tag_ids))
        .all()
    } if target_tag_ids else set()
    for tid in target_tag_ids - already:
        db.add(HostTagAssignment(host_id=host_id, tag_id=tid, created_by_id=current_user.id))
    db.commit()

    return _serialize_host_tags(db, host_id)


@router.delete(
    "/{host_id:int}/tags/{tag_id:int}",
    status_code=204,
    summary="Remove a tag from a host",
)
def remove_host_tag(
    host_id: int,
    tag_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    _host_or_404(db, project.id, host_id)
    assignment = (
        db.query(HostTagAssignment)
        .filter(HostTagAssignment.host_id == host_id, HostTagAssignment.tag_id == tag_id)
        .first()
    )
    if assignment:
        db.delete(assignment)
        db.commit()
    return Response(status_code=204)
