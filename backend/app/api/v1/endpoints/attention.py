"""Project attention ("needs help") endpoint — site-metrics arc P1.

Project-scoped read of the two-axis attention model (exposure + neglect) +
the recommended next action.  Surfaced on the Operations dashboard.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project
from app.services.attention_service import compute_project_attention

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("", summary="Project 'needs help' attention model (exposure + neglect + recommended action)")
def get_project_attention(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    return compute_project_attention(db, project.id)
