"""Agent evidence records and proposals — the agent side (v2.436.0).

* ``POST /agent/evidence`` records what the agent ran against a host and what
  came back — directly, immutably; ``GET /agent/evidence`` and
  ``…/{id}/raw`` read them back.
* ``POST /agent/proposals/{finding-text|finding|observation|endpoint-status}``
  propose a change to what the team concluded; a person accepts or rejects
  it.  ``GET /agent/proposals`` shows what happened to them.

Writes need the operator's project write role, like every write on the agent
surface (``enforce_agent_operator_access``).  Each call may carry
``agent_model`` (the model the agent is running as) — the session keeps the
latest.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import check_agent_rate_limit
from app.api.v1.endpoints.agent_common import load_agent_session
from app.db.models_agent import Agent
from app.db.session import get_db
from app.services import agent_evidence_service as evidence
from app.services import proposal_service as proposals
from app.services.agent_session_service import note_agent_model

router = APIRouter()

_MODEL = Field(None, max_length=200, description="The model you are running as (optional).")


def _who(db: Session, request: Request, agent_model: Optional[str]) -> proposals.Attribution:
    session = load_agent_session(db, request)
    note_agent_model(session, agent_model)
    return proposals.Attribution(user_id=session.started_by_id, session=session)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceCreate(BaseModel):
    host_id: int = Field(..., gt=0)
    finding_id: Optional[int] = Field(None, gt=0, description="The finding this bears on, if any.")
    finding_host_id: Optional[int] = Field(None, gt=0, description="The finding endpoint (vhost) it was run against, if any.")
    tool: str = Field(..., min_length=1, max_length=100, description="The tool or method (e.g. nmap, curl, a custom script).")
    command: Optional[str] = Field(None, max_length=10_000, description="The command as actually run, verbatim.")
    outcome: Literal["finding", "no_finding", "inconclusive", "failed", "info"] = Field(
        ..., description=(
            "finding: it demonstrated an issue; no_finding: it ran and the issue was not there; "
            "inconclusive; failed: it could not run; info: context, not a test."
        ),
    )
    summary: str = Field(..., min_length=1, max_length=10_000, description="What it showed, in a sentence or two.")
    raw_output: Optional[str] = Field(None, description="The tool's output (up to 5 MB; kept with the record).")
    observed_ip: Optional[str] = Field(None, max_length=45, description="The address actually reached.")
    executed_at: Optional[datetime] = None
    agent_model: Optional[str] = _MODEL


@router.post("/evidence", status_code=201, summary="Record what you ran against a host and what came back")
def record_evidence(
    body: EvidenceCreate,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    who = _who(db, request, body.agent_model)
    record = evidence.record_evidence(
        db, project_id=agent.project_id, host_id=body.host_id, tool=body.tool,
        outcome=body.outcome, summary=body.summary, command=body.command,
        raw_output=body.raw_output, observed_ip=body.observed_ip,
        executed_at=body.executed_at, finding_id=body.finding_id,
        finding_host_id=body.finding_host_id, agent_session_id=who.session.id,
        recorded_by_user_id=who.user_id, agent_model=who.model, agent_client=who.client,
    )
    db.commit()
    db.refresh(record)
    return evidence.serialize_evidence(record)


@router.get("/evidence", summary="List evidence records (newest first)")
def list_evidence(
    request: Request,
    host_id: Optional[int] = Query(None, gt=0),
    finding_id: Optional[int] = Query(None, gt=0),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    rows, total = evidence.list_evidence(
        db, agent.project_id, host_id=host_id, finding_id=finding_id, limit=limit, offset=offset,
    )
    return {"total": total, "items": [evidence.serialize_evidence(r) for r in rows],
            "has_more": offset + len(rows) < total}


@router.get("/evidence/{evidence_id}/raw", response_class=PlainTextResponse, summary="An evidence record's full raw output")
def evidence_raw(
    evidence_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    return evidence.read_raw_output(evidence.get_evidence(db, agent.project_id, evidence_id))


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------

class _ProposalBase(BaseModel):
    rationale: Optional[str] = Field(None, max_length=10_000, description="Why — what the reviewer should know.")
    evidence_ids: Optional[List[int]] = Field(None, description="Evidence records that support it.")
    agent_model: Optional[str] = _MODEL


class FindingTextProposal(_ProposalBase):
    finding_id: int = Field(..., gt=0)
    fields: Dict[str, str] = Field(
        ..., description=(
            "Report fields to propose: description, impact, recommendation, references, "
            "steps_to_reproduce, cvss_vector. Markdown. One proposal per field."
        ),
    )


class FindingProposal(_ProposalBase):
    title: str = Field(..., min_length=1, max_length=500)
    severity: Literal["critical", "high", "medium", "low", "info"]
    host_ids: List[int] = Field(..., min_length=1, max_length=1000)
    status: Literal["open", "confirmed"] = Field("open", description="open (under investigation) or confirmed.")
    report_text: Optional[Dict[str, str]] = Field(
        None, description="Optional report fields: description, impact, recommendation, references, steps_to_reproduce.",
    )


class ObservationProposal(_ProposalBase):
    vulnerability_id: int = Field(..., gt=0, description="The scanner observation (vulnerability row).")
    action: Literal["promote", "dismiss"] = Field(
        ..., description="promote: make it (or join it to) a confirmed finding; dismiss: false positive.",
    )
    scope: Optional[Literal["host", "issue"]] = Field(
        None, description="host: this host only (the default for dismiss); issue: every host carrying it (the default for promote).",
    )
    severity: Optional[Literal["critical", "high", "medium", "low", "info"]] = None
    summary: Optional[str] = Field(None, max_length=2000, description="Recorded on the finding's history if accepted.")


class EndpointStatusProposal(_ProposalBase):
    finding_id: int = Field(..., gt=0)
    finding_host_id: int = Field(..., gt=0, description="The endpoint (a finding_hosts row).")
    host_status: Literal["open", "remediated", "retest", "false_positive"]


def _out(db: Session, rows) -> list:
    current = proposals.current_findings(db, rows)
    return [proposals.serialize_proposal(p, current) for p in rows]


@router.post("/proposals/finding-text", status_code=201, summary="Propose report text for a finding")
def propose_finding_text(
    body: FindingTextProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    rows = proposals.propose_finding_text(
        db, agent.project_id, _who(db, request, body.agent_model), finding_id=body.finding_id,
        fields=body.fields, rationale=body.rationale, evidence_ids=body.evidence_ids,
    )
    db.commit()
    return {"proposals": _out(db, rows)}


@router.post("/proposals/finding", status_code=201, summary="Propose a new finding")
def propose_finding(
    body: FindingProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    row = proposals.propose_finding(
        db, agent.project_id, _who(db, request, body.agent_model), title=body.title,
        severity=body.severity, host_ids=body.host_ids, status=body.status,
        report_text=body.report_text, rationale=body.rationale, evidence_ids=body.evidence_ids,
    )
    db.commit()
    return _out(db, [row])[0]


@router.post("/proposals/observation", status_code=201, summary="Propose promoting or dismissing a scanner observation")
def propose_observation(
    body: ObservationProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    row = proposals.propose_observation(
        db, agent.project_id, _who(db, request, body.agent_model),
        vulnerability_id=body.vulnerability_id, action=body.action, scope=body.scope,
        severity=body.severity, summary=body.summary, rationale=body.rationale,
        evidence_ids=body.evidence_ids,
    )
    db.commit()
    return _out(db, [row])[0]


@router.post("/proposals/endpoint-status", status_code=201, summary="Propose a finding endpoint's status")
def propose_endpoint_status(
    body: EndpointStatusProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    row = proposals.propose_endpoint_status(
        db, agent.project_id, _who(db, request, body.agent_model), finding_id=body.finding_id,
        finding_host_id=body.finding_host_id, host_status=body.host_status,
        rationale=body.rationale, evidence_ids=body.evidence_ids,
    )
    db.commit()
    return _out(db, [row])[0]


@router.get("/proposals", summary="Proposals in this project and what happened to them")
def list_proposals(
    request: Request,
    status: Optional[Literal["pending", "accepted", "rejected", "superseded"]] = Query(None),
    kind: Optional[Literal[
        "finding_text", "finding_create", "observation_promote", "observation_dismiss", "endpoint_status",
    ]] = Query(None),
    finding_id: Optional[int] = Query(None, gt=0),
    mine: bool = Query(False, description="Only this session's proposals."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session_id = load_agent_session(db, request).id if mine else None
    rows, total = proposals.list_proposals(
        db, agent.project_id, status=status, kind=kind, finding_id=finding_id,
        agent_session_id=session_id, limit=limit, offset=offset,
    )
    return {"total": total, "items": _out(db, rows), "has_more": offset + len(rows) < total}
