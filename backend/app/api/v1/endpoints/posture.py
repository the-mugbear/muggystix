"""Security Posture endpoint — the manager-facing roll-up.

A single composed snapshot (deterministic label + headline measures + ranked
priorities + site/systemic/disposition breakdowns) built entirely from existing
aggregates (see posture_service). Project-scoped read; same auth + project
dependency as the attention/insights surfaces it rolls up.
"""
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project
from app.db.models_auth import User
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project
from app.services.posture_service import compute_posture

router = APIRouter(dependencies=[Depends(get_current_user)])


# The deterministic posture label. `insufficient_evidence` is the evidence gate:
# an otherwise-quiet estate with no scan evidence reads here, never as the
# reassuring `no_urgent_signals`.
PostureLabel = Literal[
    "action_required",
    "needs_assessment",
    "insufficient_evidence",
    "no_urgent_signals",
]


# Response models mirror the shape pinned by tests/test_posture.py. Every model
# allows extra keys (`extra="allow"`) so the contract can grow without silently
# dropping a field the frontend already reads — the models TYPE the response and
# lock the label domain; they do not narrow it. compute_posture still returns a
# plain dict (other internal callers may compose it); FastAPI validates that dict
# against this model at the boundary.
class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class PostureReason(_Loose):
    text: str
    severity: str


class PosturePriority(_Loose):
    kind: str
    title: str
    blast_radius: str
    action: str
    severity: str
    owner: Optional[str] = None
    link: Optional[str] = None


class PostureDecisions(_Loose):
    pending_approvals: int
    blocked_sessions: int


class PostureEvidence(_Loose):
    scan_count: int
    scan_staleness_days: Optional[int] = None


class PostureResponse(_Loose):
    label: PostureLabel
    reasons: List[PostureReason]
    headline: Dict[str, Any]
    priorities: List[PosturePriority]
    decisions: PostureDecisions
    sites: Dict[str, Any]
    systemic: Dict[str, Any]
    disposition: Dict[str, Any]
    evidence: PostureEvidence


@router.get(
    "",
    response_model=PostureResponse,
    summary="Security posture roll-up (label + headline + priorities + breakdowns)",
)
def get_posture(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Composition of the attention model, systemic insights, finding
    disposition/ownership, and the agent decision queue into one snapshot.
    The deterministic ``label`` (action_required / needs_assessment /
    insufficient_evidence / no_urgent_signals) and the ``reasons`` share the
    same signal pass as ``priorities`` so the headline and the list never
    disagree."""
    return compute_posture(db, project.id)
