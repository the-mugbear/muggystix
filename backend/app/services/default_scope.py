"""The project's one scope container (v2.9.4).

As of v2.9.4 the user never names or manages a "scope container" — a
project has exactly one scope conceptually, and the user sees a flat
list of subnet/IP entries with optional labels.  The backend Scope
model is kept as-is (no migration, no data loss on rollback), but all
write paths funnel through this helper so every new project gets one
sentinel-named scope and every upload/add operation appends to it
rather than minting a new scope.

Lived in ``endpoints/scopes.py`` until the 2026-10-01 review (B4): the
scope-domain write in ``endpoints/dns_names.py`` imported it from the router
file.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Scope

DEFAULT_SCOPE_NAME = "__default__"


def get_or_create_default_scope(db: Session, project_id: int, user_id: Optional[int] = None) -> Scope:
    """Return the project's default scope, creating it if it doesn't exist.

    If the project already has at least one scope (either a legacy
    named scope or the sentinel default), this returns the
    lowest-id existing scope so legacy projects land in a stable,
    deterministic "first" scope rather than minting yet another one.
    Projects with zero scopes get a freshly-created sentinel scope
    named ``__default__``.
    """
    existing = (
        db.query(Scope)
        .filter(Scope.project_id == project_id)
        .order_by(Scope.id.asc())
        .first()
    )
    if existing:
        return existing
    scope = Scope(
        name=DEFAULT_SCOPE_NAME,
        description="Project scope",
        project_id=project_id,
        uploaded_by_id=user_id,
    )
    db.add(scope)
    db.commit()
    db.refresh(scope)
    return scope
