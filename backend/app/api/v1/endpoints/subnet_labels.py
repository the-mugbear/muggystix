"""Subnet label management (v2.86.0).

Project-scoped labels ("internet-facing", "PCI", "lab", "decommission")
that can be attached to one or more subnets.  The Hosts inventory page
gains a ``subnet_labels`` filter that walks
Host → HostSubnetMapping → Subnet → SubnetLabelAssignment so an operator
can carve large host inventories by infrastructure boundary rather than
per-host tagging.

Mounted under the ``/scopes`` prefix on the project router so the URL
shape mirrors the existing scope/subnet endpoints:

    GET    /projects/{project_id}/scopes/subnet-labels
    POST   /projects/{project_id}/scopes/subnet-labels
    PATCH  /projects/{project_id}/scopes/subnet-labels/{label_id}
    DELETE /projects/{project_id}/scopes/subnet-labels/{label_id}
    POST   /projects/{project_id}/scopes/subnet-labels/{label_id}/subnets
        — bulk-apply one label across many subnets
    PUT    /projects/{project_id}/scopes/subnets/{subnet_id}/labels
        — replace a subnet's full label set
    POST   /projects/{project_id}/scopes/subnets/{subnet_id}/labels/{label_id}
    DELETE /projects/{project_id}/scopes/subnets/{subnet_id}/labels/{label_id}

Permission model matches the rest of the scopes router: any authenticated
project member can READ labels; Analyst+ can CRUD and assign/unassign.
The host-tag surface is deliberately more permissive (any member can
manage) because tags are personal-annotation-shaped; subnet labels are
scope-structure-shaped and follow the scope CRUD gate instead.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, distinct
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import (
    HostSubnetMapping,
    Scope,
    Subnet,
    SubnetLabel,
    SubnetLabelAssignment,
)
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.schemas import (
    SubnetLabel as SubnetLabelSchema,
    SubnetLabelBulkAssign,
    SubnetLabelBulkAssignMany,
    SubnetLabelCreate,
    SubnetLabelInfo,
    SubnetLabelUpdate,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Counts(BaseModel):
    subnet_count: int = 0
    host_count: int = 0


def _label_or_404(db: Session, project_id: int, label_id: int) -> SubnetLabel:
    """Look up a label restricted to the calling project.

    Always project-scope the lookup — never trust a label_id from the
    body/path alone, since a guessed ID from another project would
    otherwise be writable.  Mirrors the ``_tag_or_404`` pattern in
    ``host_tags.py``.
    """
    label = (
        db.query(SubnetLabel)
        .filter(SubnetLabel.id == label_id, SubnetLabel.project_id == project_id)
        .first()
    )
    if not label:
        raise HTTPException(status_code=404, detail="Subnet label not found")
    return label


def _subnet_or_404(db: Session, project_id: int, subnet_id: int) -> Subnet:
    """Resolve a subnet that belongs to the calling project.

    Subnet has no direct project_id column; it goes through Scope.  The
    join is cheap and the constraint is essential — a label assignment
    against a subnet from another project would write a row this project
    can read back, leaking infrastructure boundaries.
    """
    subnet = (
        db.query(Subnet)
        .join(Scope, Scope.id == Subnet.scope_id)
        .filter(Subnet.id == subnet_id, Scope.project_id == project_id)
        .first()
    )
    if not subnet:
        raise HTTPException(status_code=404, detail="Subnet not found")
    return subnet


def _counts_for_labels(db: Session, project_id: int) -> dict[int, _Counts]:
    """Return per-label ``subnet_count`` + distinct ``host_count`` for the project.

    ``host_count`` MUST be COUNT(DISTINCT host_id) — a host can sit in
    multiple subnets that share a label (overlapping CIDRs, smaller
    blocks nested inside a labeled supernet, etc.), so counting
    assignment rows would double-count.  This is why the existing
    HostTag pattern (count of assignments) doesn't translate directly.
    """
    # subnet count: one row per (label, subnet)
    subnet_rows = (
        db.query(
            SubnetLabelAssignment.label_id,
            func.count(SubnetLabelAssignment.id),
        )
        .join(SubnetLabel, SubnetLabel.id == SubnetLabelAssignment.label_id)
        .filter(SubnetLabel.project_id == project_id)
        .group_by(SubnetLabelAssignment.label_id)
        .all()
    )
    # distinct host count: walk SubnetLabelAssignment → HostSubnetMapping
    host_rows = (
        db.query(
            SubnetLabelAssignment.label_id,
            func.count(distinct(HostSubnetMapping.host_id)),
        )
        .join(SubnetLabel, SubnetLabel.id == SubnetLabelAssignment.label_id)
        .join(HostSubnetMapping, HostSubnetMapping.subnet_id == SubnetLabelAssignment.subnet_id)
        .filter(SubnetLabel.project_id == project_id)
        .group_by(SubnetLabelAssignment.label_id)
        .all()
    )
    out: dict[int, _Counts] = {}
    for label_id, n in subnet_rows:
        out.setdefault(label_id, _Counts()).subnet_count = int(n or 0)
    for label_id, n in host_rows:
        out.setdefault(label_id, _Counts()).host_count = int(n or 0)
    return out


def _to_schema(label: SubnetLabel, counts: _Counts | None = None) -> SubnetLabelSchema:
    c = counts or _Counts()
    return SubnetLabelSchema(
        id=label.id,
        project_id=label.project_id,
        name=label.name,
        color=label.color,
        created_at=label.created_at,
        subnet_count=c.subnet_count,
        host_count=c.host_count,
    )


# ---------------------------------------------------------------------------
# Label definition CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/subnet-labels",
    response_model=List[SubnetLabelSchema],
    summary="List project subnet labels with subnet + distinct-host counts",
)
def list_subnet_labels(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    labels = (
        db.query(SubnetLabel)
        .filter(SubnetLabel.project_id == project.id)
        .order_by(SubnetLabel.name)
        .all()
    )
    counts = _counts_for_labels(db, project.id)
    return [_to_schema(lbl, counts.get(lbl.id)) for lbl in labels]


@router.post(
    "/subnet-labels",
    response_model=SubnetLabelSchema,
    status_code=201,
    summary="Create a subnet label",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def create_subnet_label(
    payload: SubnetLabelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Label name cannot be empty")
    label = SubnetLabel(
        project_id=project.id,
        name=name,
        color=payload.color,
        created_by_id=current_user.id,
    )
    db.add(label)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A subnet label named '{name}' already exists in this project",
        )
    db.refresh(label)
    return _to_schema(label)


@router.patch(
    "/subnet-labels/{label_id:int}",
    response_model=SubnetLabelSchema,
    summary="Rename or recolor a subnet label",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def update_subnet_label(
    label_id: int,
    payload: SubnetLabelUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    label = _label_or_404(db, project.id, label_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Label name cannot be empty")
        label.name = name
    if payload.color is not None:
        label.color = payload.color or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A subnet label with that name already exists in this project",
        )
    db.refresh(label)
    counts = _counts_for_labels(db, project.id)
    return _to_schema(label, counts.get(label.id))


@router.delete(
    "/subnet-labels/{label_id:int}",
    status_code=204,
    summary="Delete a subnet label (removes all its assignments)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def delete_subnet_label(
    label_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    label = _label_or_404(db, project.id, label_id)
    db.delete(label)  # assignments cascade via FK ondelete=CASCADE + ORM cascade
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Assignment routes
# ---------------------------------------------------------------------------


@router.put(
    "/subnets/{subnet_id:int}/labels",
    response_model=List[SubnetLabelInfo],
    summary="Replace the full label set on a subnet",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def replace_subnet_labels(
    subnet_id: int,
    payload: SubnetLabelBulkAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Idempotent: PUT the desired ``label_ids`` set; any labels not
    listed are detached, any in the list not yet attached are attached.

    Every label_id is project-scoped via ``_label_or_404`` so a guessed
    ID from another project 404s instead of leaking attachment."""
    _subnet_or_404(db, project.id, subnet_id)

    target_ids: set[int] = set()
    for lid in payload.label_ids:
        _label_or_404(db, project.id, lid)
        target_ids.add(lid)

    existing = (
        db.query(SubnetLabelAssignment)
        .filter(SubnetLabelAssignment.subnet_id == subnet_id)
        .all()
    )
    existing_ids = {a.label_id for a in existing}

    for a in existing:
        if a.label_id not in target_ids:
            db.delete(a)
    for lid in target_ids - existing_ids:
        db.add(SubnetLabelAssignment(
            subnet_id=subnet_id, label_id=lid, created_by_id=current_user.id,
        ))
    db.commit()

    rows = (
        db.query(SubnetLabel)
        .join(SubnetLabelAssignment, SubnetLabelAssignment.label_id == SubnetLabel.id)
        .filter(SubnetLabelAssignment.subnet_id == subnet_id)
        .order_by(SubnetLabel.name)
        .all()
    )
    return [SubnetLabelInfo(id=r.id, name=r.name, color=r.color) for r in rows]


@router.post(
    "/subnets/{subnet_id:int}/labels/{label_id:int}",
    response_model=SubnetLabelInfo,
    summary="Attach a single label to a subnet (idempotent)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def attach_subnet_label(
    subnet_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    _subnet_or_404(db, project.id, subnet_id)
    label = _label_or_404(db, project.id, label_id)
    existing = (
        db.query(SubnetLabelAssignment)
        .filter(
            SubnetLabelAssignment.subnet_id == subnet_id,
            SubnetLabelAssignment.label_id == label_id,
        )
        .first()
    )
    if not existing:
        db.add(SubnetLabelAssignment(
            subnet_id=subnet_id, label_id=label_id, created_by_id=current_user.id,
        ))
        db.commit()
    return SubnetLabelInfo(id=label.id, name=label.name, color=label.color)


@router.delete(
    "/subnets/{subnet_id:int}/labels/{label_id:int}",
    status_code=204,
    summary="Detach a single label from a subnet",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def detach_subnet_label(
    subnet_id: int,
    label_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    _subnet_or_404(db, project.id, subnet_id)
    _label_or_404(db, project.id, label_id)
    assignment = (
        db.query(SubnetLabelAssignment)
        .filter(
            SubnetLabelAssignment.subnet_id == subnet_id,
            SubnetLabelAssignment.label_id == label_id,
        )
        .first()
    )
    if assignment:
        db.delete(assignment)
        db.commit()
    return Response(status_code=204)


@router.post(
    "/subnet-labels/{label_id:int}/subnets",
    response_model=SubnetLabelSchema,
    summary="Bulk-apply one label across many subnets",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def bulk_apply_subnet_label(
    label_id: int,
    payload: SubnetLabelBulkAssignMany,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Attach ``label_id`` to every subnet in ``subnet_ids``.

    All subnet_ids must belong to the project (any foreign id 404s the
    whole request — no partial application).  Re-applying to a subnet
    that already carries the label is a no-op for that subnet, not an
    error: the operation is idempotent across the input set.
    """
    label = _label_or_404(db, project.id, label_id)

    # Validate every subnet belongs to the project up front so a bad ID
    # doesn't half-apply the bulk operation.
    valid_subnet_ids: set[int] = set()
    for sid in payload.subnet_ids:
        _subnet_or_404(db, project.id, sid)
        valid_subnet_ids.add(sid)

    already = {
        row[0]
        for row in db.query(SubnetLabelAssignment.subnet_id)
        .filter(
            SubnetLabelAssignment.label_id == label_id,
            SubnetLabelAssignment.subnet_id.in_(valid_subnet_ids),
        )
        .all()
    } if valid_subnet_ids else set()
    for sid in valid_subnet_ids - already:
        db.add(SubnetLabelAssignment(
            subnet_id=sid, label_id=label_id, created_by_id=current_user.id,
        ))
    db.commit()

    counts = _counts_for_labels(db, project.id)
    return _to_schema(label, counts.get(label.id))
