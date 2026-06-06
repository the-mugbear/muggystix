"""Saved Hosts-page filter views (per-user, per-project).

Carved out of ``hosts.py`` in v2.71.0 under the file-size policy: a
self-contained CRUD cluster on the ``HostFilterView`` model, unrelated to
host listing/serialization.  Endpoint paths are unchanged — this router
is mounted at the same ``/hosts`` prefix, so ``/hosts/views`` keeps
working across the split.

A "view" is a named bundle of Hosts page filter state the user can save
and re-apply.  The filter blob (``filter_json``) is opaque to the backend
— the frontend owns its shape — so adding or renaming filter dimensions
doesn't require a schema migration.  Sharing across users is intentionally
NOT supported; views are strictly personal.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import HostFilterView
from app.db.models_auth import User
from app.db.models_project import Project
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project

router = APIRouter(dependencies=[Depends(get_current_user)])


class HostFilterViewSchema(BaseModel):
    id: int
    name: str
    filter_json: dict
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class HostFilterViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    filter_json: dict


class HostFilterViewUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    filter_json: Optional[dict] = None


@router.get(
    "/views",
    response_model=List[HostFilterViewSchema],
    summary="List the current user's saved Hosts page filter views",
)
def list_host_filter_views(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Return the current user's saved Hosts page views for this project."""
    views = (
        db.query(HostFilterView)
        .filter(
            HostFilterView.user_id == current_user.id,
            HostFilterView.project_id == project.id,
        )
        .order_by(HostFilterView.updated_at.desc().nullslast(), HostFilterView.created_at.desc())
        .all()
    )
    return views


@router.post(
    "/views",
    response_model=HostFilterViewSchema,
    status_code=201,
    summary="Save a new Hosts page filter view",
)
def create_host_filter_view(
    body: HostFilterViewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Save a new Hosts page view for the current user.

    Returns 409 if the user already has a view with the same name in
    this project — the unique constraint enforces it; we surface a
    friendly error before SQLAlchemy raises.
    """
    existing = (
        db.query(HostFilterView)
        .filter(
            HostFilterView.user_id == current_user.id,
            HostFilterView.project_id == project.id,
            HostFilterView.name == body.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"You already have a view named '{body.name}' in this project.",
        )

    view = HostFilterView(
        user_id=current_user.id,
        project_id=project.id,
        name=body.name,
        filter_json=body.filter_json,
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return view


@router.patch(
    "/views/{view_id}",
    response_model=HostFilterViewSchema,
    summary="Rename a saved view or replace its filter blob",
)
def update_host_filter_view(
    view_id: int,
    body: HostFilterViewUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Rename a view or replace its filter blob.

    Permissions: a view can only be touched by its owner.  Cross-user
    access returns 404 (not 403) so the existence of another user's
    view isn't leaked.
    """
    view = (
        db.query(HostFilterView)
        .filter(
            HostFilterView.id == view_id,
            HostFilterView.user_id == current_user.id,
            HostFilterView.project_id == project.id,
        )
        .first()
    )
    if not view:
        raise HTTPException(status_code=404, detail="Saved view not found.")

    if body.name is not None and body.name != view.name:
        # Rename collision check before commit.
        clash = (
            db.query(HostFilterView)
            .filter(
                HostFilterView.user_id == current_user.id,
                HostFilterView.project_id == project.id,
                HostFilterView.name == body.name,
                HostFilterView.id != view_id,
            )
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"You already have a view named '{body.name}' in this project.",
            )
        view.name = body.name

    if body.filter_json is not None:
        view.filter_json = body.filter_json

    db.commit()
    db.refresh(view)
    return view


@router.delete(
    "/views/{view_id}",
    status_code=204,
    summary="Delete a saved Hosts page filter view",
)
def delete_host_filter_view(
    view_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Delete one of the current user's saved views."""
    view = (
        db.query(HostFilterView)
        .filter(
            HostFilterView.id == view_id,
            HostFilterView.user_id == current_user.id,
            HostFilterView.project_id == project.id,
        )
        .first()
    )
    if not view:
        raise HTTPException(status_code=404, detail="Saved view not found.")
    db.delete(view)
    db.commit()
    return Response(status_code=204)
