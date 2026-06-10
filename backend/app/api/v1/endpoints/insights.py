"""Insights endpoints — derived, cross-host analytics over project data.

Currently surfaces the per-subnet insights view (exposure + neglect +
hygiene, worst-first) that answers "which network ranges are neglected or
in bad shape?".  Project-scoped read; same auth + project dependency as the
attention surface it extends.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project
from app.services.subnet_insight_service import compute_subnet_insights

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/subnets", summary="Per-subnet insights (exposure + neglect + hygiene, worst-first)")
def get_subnet_insights(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=500, description="Page size (worst-first)."),
    offset: int = Query(0, ge=0, description="Row offset for pagination."),
) -> Dict[str, Any]:
    """Paginated, worst-first.  ``subnets`` is the requested page; ``total`` is
    the full count and ``totals`` is project-wide (not page-scoped).
    ``adopted=False`` when the project has no scoped subnets — the UI shows an
    onboarding empty state instead of an empty table."""
    return compute_subnet_insights(db, project.id, limit=limit, offset=offset)
