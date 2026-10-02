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
from app.api.deps import get_current_user
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services import agent_evidence_service as evidence
from app.services import proposal_service as proposals
from app.services.finding_actions import finding_actor

router = APIRouter(dependencies=[Depends(get_current_user)])

_Status = Literal["pending", "accepted", "rejected", "superseded"]
_Kind = Literal["finding_text", "finding_create", "observation_promote", "observation_dismiss", "endpoint_status"]


@router.get("/proposals", summary="Agent proposals in this project")
def list_proposals(
    status: Optional[_Status] = Query("pending"),
    kind: Optional[_Kind] = Query(None),
    finding_id: Optional[int] = Query(None, gt=0),
    host_id: Optional[int] = Query(None, gt=0),
    agent_session_id: Optional[int] = Query(None, gt=0),
    mine: bool = Query(False, description="Only proposals about findings you authored or own — the ones you are notified about."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    rows, total = proposals.list_proposals(
        db, project.id, status=status, kind=kind, finding_id=finding_id, host_id=host_id,
        agent_session_id=agent_session_id, mine_user_id=user.id if mine else None,
        limit=limit, offset=offset,
    )
    return {"total": total, "items": proposals.serialize_many(db, rows), "has_more": offset + len(rows) < total}


@router.get("/proposals/summary", summary="Pending proposals per kind (the top-bar count)")
def proposals_summary(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """The project's pending proposals, and the caller's own (about findings
    they authored or own).  v2.440.0: an admin's review of every finding put
    the whole run in every member's top bar; the badge shows a person their
    own, and a project admin (who may accept any report text) the project's —
    ``viewer_is_project_admin`` says which view is theirs by default."""
    counts = proposals.pending_counts(db, project.id)
    mine = proposals.pending_counts(db, project.id, mine_user_id=user.id)
    return {
        "pending": sum(counts.values()), "by_kind": counts,
        "pending_mine": sum(mine.values()), "by_kind_mine": mine,
        "viewer_is_project_admin": finding_actor(db, project.id, user).is_project_admin,
    }


class DecideBody(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)
    # Report text only: accept with this text instead of the proposed text.
    edited_value: Optional[str] = Field(None, max_length=32768)


def _decide(
    db: Session, project_id: int, proposal_id: int, user: User, action: str, body: DecideBody,
    *, serialize: bool = True,
):
    """Decide one proposal and commit.  Returns its row as the routes return
    it, or None with ``serialize=False`` (the bulk route reports ids only —
    it used to build and discard each row, about five statements apiece;
    review 2026-10-01 N8)."""
    # Locked until the commit below: a second decision on it waits, then
    # finds it decided (409) instead of applying it again.
    # One lock order for every decision — a report-text proposal's finding
    # first, then the proposal (H1; `lock_for_decision` says why).
    proposal = proposals.lock_for_decision(db, project_id, proposal_id)
    try:
        if action == "accept":
            proposals.accept_proposal(db, proposal, user, edited_value=body.edited_value, note=body.note)
        else:
            proposals.reject_proposal(db, proposal, user, note=body.note)
    except HTTPException:
        # Keep the reason on the proposal (it stays pending) — then refuse.
        db.commit()
        raise
    # Built before the commit, which expires the row and what it points at
    # (N8, as the host-test routes do).  The flush puts the decision where the
    # lazy loads below read it.
    db.flush()
    data = proposals.serialize_many(db, [proposal])[0] if serialize else None
    db.commit()
    return data


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
    others were not.  Accepting never picks between several drafts of one
    field: accepting the first would supersede the rest by list order, so
    those are left pending for a person to choose on the finding."""
    done, failed = [], []
    competing = (
        proposals.competing_drafts(db, project.id, body.ids) if body.action == "accept" else set()
    )
    for pid in dict.fromkeys(body.ids):
        if pid in competing:
            failed.append({"id": pid, "status_code": 409, "detail": proposals.COMPETING_DRAFTS_DETAIL})
            continue
        try:
            _decide(db, project.id, pid, user, body.action, DecideBody(note=body.note), serialize=False)
            done.append(pid)
        except HTTPException as exc:
            failed.append({"id": pid, "status_code": exc.status_code, "detail": exc.detail})
    return {"decided": done, "failed": failed}


# ---------------------------------------------------------------------------
# Evidence records (agents write them; a person records a test's result
# through POST /host-tests/{id}/result)
# ---------------------------------------------------------------------------

@router.get("/evidence", summary="Agent evidence records (newest first)")
def list_evidence(
    host_test_id: Optional[int] = Query(None, gt=0),
    host_id: Optional[int] = Query(None, gt=0),
    finding_id: Optional[int] = Query(None, gt=0),
    agent_session_id: Optional[int] = Query(None, gt=0),
    unlinked: bool = Query(False, description="Only records that answer no host test."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    rows, total = evidence.list_evidence(
        db, project.id, host_id=host_id, finding_id=finding_id, host_test_id=host_test_id,
        agent_session_id=agent_session_id, unlinked=unlinked, limit=limit, offset=offset,
    )
    return {"total": total, "items": [evidence.serialize_evidence(r) for r in rows],
            "has_more": offset + len(rows) < total}


class _FindingFromEvidence(BaseModel):
    # Ignored when the record's test confirms a scanner observation: that is
    # the observation's promotion and the issue names and rates the finding.
    title: Optional[str] = Field(None, max_length=500)
    severity: Optional[Literal["critical", "high", "medium", "low", "info"]] = None
    status: Literal["open", "confirmed"] = "confirmed"


@router.post("/evidence/{evidence_id}/finding", status_code=201,
             summary="Create a finding from an evidence record that showed an issue")
def finding_from_evidence(
    body: _FindingFromEvidence,
    evidence_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    finding, joined = evidence.create_finding_from_evidence(
        db, project.id, evidence_id, title=body.title, severity=body.severity,
        status=body.status, actor_id=user.id,
    )
    # ``joined_issue``: the record's test confirmed a scanner observation, so
    # this was that observation's promotion (the issue's finding, not a new one).
    # ``status`` is the finding's as it now stands — joining a finding the team
    # already concluded leaves its status alone (review 2026-10-01 R9), so it
    # may differ from the one asked for.  Built before the commit expires it.
    data = {"finding_id": finding.id, "title": finding.title, "severity": finding.severity,
            "status": finding.status, "evidence_id": evidence_id, "joined_issue": joined}
    db.commit()
    return data


@router.get("/evidence/{evidence_id}/raw", response_class=PlainTextResponse, summary="An evidence record's raw output")
def evidence_raw(
    evidence_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return evidence.read_raw_output(evidence.get_evidence(db, project.id, evidence_id))
