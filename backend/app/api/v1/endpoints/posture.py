"""Security Posture endpoint — the manager-facing roll-up.

A single composed snapshot (deterministic label + headline measures + ranked
priorities + site/systemic/disposition breakdowns) built entirely from existing
aggregates (see posture_service). Project-scoped read; same auth + project
dependency as the attention/insights surfaces it rolls up.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project
from app.services.posture_service import compute_posture

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("", summary="Security posture roll-up (label + headline + priorities + breakdowns)")
def get_posture(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Composition of the attention model, systemic insights, finding
    disposition/ownership, and the agent decision queue into one snapshot.
    The deterministic ``label`` (action_required / needs_assessment /
    no_urgent_signals) and the ``reasons`` share the same signal pass as
    ``priorities`` so the headline and the list never disagree."""
    return compute_posture(db, project.id)
