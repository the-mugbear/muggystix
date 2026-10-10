"""The project's one scope.

A project has exactly ONE scope (owner, 2026-10-10; ``uq_scopes_project`` in
revision ``e1b4c7d0a2f5``): a container for its subnet / address entries and
its domain entries.  It has no name and nobody manages it — a person sees a
flat list of entries.  Every path that needs it asks here; nothing else
constructs a ``Scope``.

Lived in ``endpoints/scopes.py`` until the 2026-10-01 review (B4): the
scope-domain write in ``endpoints/dns_names.py`` imported it from the router
file.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Scope


def project_scope(db: Session, project_id: int) -> Optional[Scope]:
    """The project's scope, or ``None`` when it has none yet.  For a READ that
    must not create anything (an agent's scope reads)."""
    return db.query(Scope).filter(Scope.project_id == project_id).first()


def get_or_create_default_scope(db: Session, project_id: int, user_id: Optional[int] = None) -> Scope:
    """Return the project's scope, creating it when the project has none.

    Two first requests of a new project may both find none: the insert runs in
    a savepoint, and the one that loses the unique constraint reads the
    winner's row instead of failing its request.  Commits, as it always has.
    """
    existing = project_scope(db, project_id)
    if existing:
        return existing
    scope = Scope(description="Project scope", project_id=project_id, uploaded_by_id=user_id)
    try:
        with db.begin_nested():
            db.add(scope)
    except IntegrityError:
        won = project_scope(db, project_id)
        if won is None:
            raise
        return won
    db.commit()
    db.refresh(scope)
    return scope
