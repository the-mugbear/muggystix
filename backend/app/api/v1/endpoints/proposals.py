"""Agent proposals and evidence records — the reviewer's side (v2.436.0).

Mounted under ``/projects/{project_id}``.  Any member reads; deciding needs
project analyst, and accepting report text needs what editing it needs — the
finding's author or a project admin (the same rule, applied by the same
service).  Accepting runs the change as the person who accepts it.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services import agent_evidence_service as evidence
from app.services import proposal_service as proposals

router = APIRouter(dependencies=[Depends(get_current_user)])

_Status = Literal["pending", "accepted", "rejected", "superseded"]
_Kind = Literal["finding_text", "finding_create", "observation_promote", "observation_dismiss", "endpoint_status"]


def _out(db: Session, rows) -> list:
    current = proposals.current_findings(db, rows)
    return [proposals.serialize_proposal(p, current) for p in rows]


@router.get("/proposals", summary="Agent proposals in this project")
def list_proposals(
    status: Optional[_Status] = Query("pending"),
    kind: Optional[_Kind] = Query(None),
    finding_id: Optional[int] = Query(None, gt=0),
    host_id: Optional[int] = Query(None, gt=0),
    agent_session_id: Optional[int] = Query(None, gt=0),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    rows, total = proposals.list_proposals(
        db, project.id, status=status, kind=kind, finding_id=finding_id, host_id=host_id,
        agent_session_id=agent_session_id, limit=limit, offset=offset,
    )
    return {"total": total, "items": _out(db, rows), "has_more": offset + len(rows) < total}


@router.get("/proposals/summary", summary="Pending proposals per kind (the top-bar count)")
def proposals_summary(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    counts = proposals.pending_counts(db, project.id)
    return {"pending": sum(counts.values()), "by_kind": counts}


class DecideBody(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)
    # Report text only: accept with this text instead of the proposed text.
    edited_value: Optional[str] = Field(None, max_length=32768)


def _decide(db: Session, project_id: int, proposal_id: int, user: User, action: str, body: DecideBody):
    proposal = proposals.get_proposal(db, project_id, proposal_id)
    try:
        if action == "accept":
            proposals.accept_proposal(db, proposal, user, edited_value=body.edited_value, note=body.note)
        else:
            proposals.reject_proposal(db, proposal, user, note=body.note)
    except HTTPException:
        # Keep the reason on the proposal (it stays pending) — then refuse.
        db.commit()
        raise
    db.commit()
    return _out(db, [proposal])[0]


@router.post("/proposals/{proposal_id}/accept", summary="Accept a proposal: apply it as you")
def accept_proposal(
    body: DecideBody = DecideBody(),
    proposal_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    return _decide(db, project.id, proposal_id, user, "accept", body)


@router.post("/proposals/{proposal_id}/reject", summary="Reject a proposal")
def reject_proposal(
    body: DecideBody = DecideBody(),
    proposal_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    return _decide(db, project.id, proposal_id, user, "reject", body)


class BulkBody(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=200)
    action: Literal["accept", "reject"]
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/proposals/bulk", summary="Accept or reject several proposals; each is decided on its own")
def bulk_decide(
    body: BulkBody,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """One failure (no right to edit that finding, a target that changed) does
    not stop the rest; the response says which were decided and why the
    others were not."""
    done, failed = [], []
    for pid in dict.fromkeys(body.ids):
        try:
            _decide(db, project.id, pid, user, body.action, DecideBody(note=body.note))
            done.append(pid)
        except HTTPException as exc:
            failed.append({"id": pid, "status_code": exc.status_code, "detail": exc.detail})
    return {"decided": done, "failed": failed}


# ---------------------------------------------------------------------------
# Evidence records (read; agents write them)
# ---------------------------------------------------------------------------

@router.get("/evidence", summary="Agent evidence records (newest first)")
def list_evidence(
    host_id: Optional[int] = Query(None, gt=0),
    finding_id: Optional[int] = Query(None, gt=0),
    agent_session_id: Optional[int] = Query(None, gt=0),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    rows, total = evidence.list_evidence(
        db, project.id, host_id=host_id, finding_id=finding_id,
        agent_session_id=agent_session_id, limit=limit, offset=offset,
    )
    return {"total": total, "items": [evidence.serialize_evidence(r) for r in rows],
            "has_more": offset + len(rows) < total}


@router.get("/evidence/{evidence_id}/raw", response_class=PlainTextResponse, summary="An evidence record's raw output")
def evidence_raw(
    evidence_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return evidence.read_raw_output(evidence.get_evidence(db, project.id, evidence_id))
