"""Saved and shared Query-page queries (v2.509.0).

The owner, 2026-10-16: saved and shared searches, as the teams know them from
their log tools.  A saved query is one project's: its creator's alone, or — ``shared``
— every member's to see and run.  Only the creator, or a project admin,
changes or deletes it; a shared query whose creator's account was deleted is
then a project admin's.  Saving checks the query reads (grammar and names),
never runs it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.db.models import SavedQuery
from app.db.models_auth import User
from app.services import query_service
from app.services.finding_actions import finding_actor


def check_query(text: str) -> None:
    """The query is one ``run`` would accept — ``query_service.check``, the
    same plan ``run`` builds, without reading the database (external review
    2026-10-16, A4: this used to check the grammar and the dataset only, so
    `hosts | where bogus=1` saved and then failed to run)."""
    query_service.check(text)


def _visible(db: Session, project_id: int, user: User):
    return (
        db.query(SavedQuery)
        .options(joinedload(SavedQuery.created_by))
        .filter(SavedQuery.project_id == project_id,
                or_(SavedQuery.shared.is_(True), SavedQuery.created_by_id == user.id))
    )


def serialize(row: SavedQuery, user: User, is_project_admin: bool) -> Dict[str, Any]:
    mine = row.created_by_id == user.id
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "query": row.query,
        "shared": bool(row.shared),
        "created_by": (row.created_by.full_name or row.created_by.username) if row.created_by else None,
        "mine": mine,
        "can_modify": mine or is_project_admin,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_visible(db: Session, project_id: int, user: User) -> List[Dict[str, Any]]:
    """Mine, then the team's shared ones, each by name."""
    admin = finding_actor(db, project_id, user).is_project_admin
    rows = _visible(db, project_id, user).all()
    rows.sort(key=lambda r: (r.created_by_id != user.id, r.name.lower(), r.id))
    return [serialize(r, user, admin) for r in rows]


def _get_for_change(db: Session, project_id: int, user: User, query_id: int) -> SavedQuery:
    row = _visible(db, project_id, user).filter(SavedQuery.id == query_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    if row.created_by_id != user.id and not finding_actor(db, project_id, user).is_project_admin:
        raise HTTPException(status_code=403, detail="Only its creator or a project admin can change this query")
    return row


def _commit_name(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="You already have a saved query with that name")


def _name(name: str) -> str:
    """The name as stored: trimmed, and never blank (second review, I7 —
    the request's own check counted the spaces)."""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A saved query needs a name")
    return name


def create(db: Session, project_id: int, user: User, *, name: str, query: str,
           description: Optional[str], shared: bool) -> Dict[str, Any]:
    check_query(query)
    row = SavedQuery(project_id=project_id, created_by_id=user.id, name=_name(name),
                     description=(description or "").strip() or None, query=query.strip(), shared=shared)
    db.add(row)
    _commit_name(db)
    db.refresh(row)
    return serialize(row, user, finding_actor(db, project_id, user).is_project_admin)


def update(db: Session, project_id: int, user: User, query_id: int, changes: Dict[str, Any]) -> Dict[str, Any]:
    row = _get_for_change(db, project_id, user, query_id)
    if "query" in changes and changes["query"] is not None:
        check_query(changes["query"])
        row.query = changes["query"].strip()
    if changes.get("name") is not None:
        row.name = _name(changes["name"])
    if "description" in changes:
        row.description = (changes["description"] or "").strip() or None
    if changes.get("shared") is not None:
        row.shared = bool(changes["shared"])
    _commit_name(db)
    db.refresh(row)
    return serialize(row, user, finding_actor(db, project_id, user).is_project_admin)


def delete(db: Session, project_id: int, user: User, query_id: int) -> None:
    row = _get_for_change(db, project_id, user, query_id)
    db.delete(row)
    db.commit()
