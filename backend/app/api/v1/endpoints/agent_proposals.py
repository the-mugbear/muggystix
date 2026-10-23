"""Agent evidence records and proposals — the agent side (v2.436.0).

* ``POST /agent/evidence`` records what the agent ran against a host and what
  came back — directly, immutably; ``GET /agent/evidence`` and
  ``…/{id}/raw`` read them back.
* ``POST /agent/proposals/{finding-text|finding|observation|endpoint-status|subnet-details}``
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
from app.api.v1.endpoints.agent_common import load_agent_session, require_project_host
from app.api.v1.endpoints.agent_schemas import AgentRowsPage, agent_page
from app.db.models_agent import Agent
from app.db.session import get_db
from app.services import agent_evidence_service as evidence
from app.services import proposal_service as proposals
from app.services.agent_session_service import note_agent_model

router = APIRouter()

_MODEL = Field(None, max_length=100, description="The model you are running as (optional).")


def _who(db: Session, request: Request, agent_model: Optional[str]) -> proposals.Attribution:
    session = load_agent_session(db, request)
    note_agent_model(session, agent_model)
    return proposals.Attribution(user_id=request.state.key_operator_id, session=session)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceCreate(BaseModel):
    host_test_id: Optional[int] = Field(None, gt=0)
    request_key: Optional[str] = Field(None, min_length=1, max_length=100, description="Stable retry key; required for host-test evidence.")
    host_id: Optional[int] = Field(
        None, gt=0,
        description=(
            "The host it was run against. Leave it out only for context about no single host "
            "(outcome info, no host_test_id, no finding_host_id) — a domain-wide lookup, say."
        ),
    )
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
    # Capped by the evidence service (review 2026-10-01 R10): over 5 MB is a
    # 413, checked BEFORE the text is hashed.  Deliberately no ``max_length``
    # here — that makes it a 422 whose body echoes the oversize input, and
    # the agent guide documents 413.
    raw_output: Optional[str] = Field(
        None, description="The tool's output (up to 5 MB; kept with the record; over that is a 413).",
    )
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
        host_test_id=body.host_test_id, request_key=body.request_key,
    )
    # Serialized BEFORE the commit, like the host-test routes: expire-on-commit
    # makes every attribute read afterwards a SELECT (and `refresh` another).
    db.flush()
    payload = evidence.serialize_evidence(record)
    db.commit()
    return payload


@router.get("/evidence", response_model=AgentRowsPage, summary="List evidence records (newest first)")
def list_evidence(
    request: Request,
    host_test_id: Optional[int] = Query(None, gt=0),
    host_id: Optional[int] = Query(None, gt=0),
    finding_id: Optional[int] = Query(None, gt=0),
    agent_session_id: Optional[int] = Query(
        None, gt=0, description="Only what this agent session recorded.",
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    preview: bool = Query(
        True,
        description=(
            "false: leave out each record's 2,000-character output preview "
            "(`raw_output_preview` is null; `raw_output_truncated_in_preview` then says "
            "whether there is output to read whole with /evidence/{id}/raw or /raw-chunk)."
        ),
    ),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    require_project_host(db, agent.project_id, host_id)
    rows, total = evidence.list_evidence(
        db, agent.project_id, host_id=host_id, finding_id=finding_id,
        host_test_id=host_test_id, agent_session_id=agent_session_id,
        limit=limit, offset=offset, with_preview=preview,
    )
    return agent_page([evidence.serialize_evidence(r, preview=preview) for r in rows],
                      total=total, limit=limit, offset=offset)


@router.get("/evidence/{evidence_id}/raw", response_class=PlainTextResponse, summary="An evidence record's full raw output")
def evidence_raw(
    evidence_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    return evidence.read_raw_output(evidence.get_evidence(db, agent.project_id, evidence_id))


@router.get("/evidence/{evidence_id}", summary="One evidence record (its raw output's size and hash)")
def get_evidence(
    evidence_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The record as the list shows it (2.519.0): the 2 KB preview, the
    output's size (``raw_output_bytes``) and SHA-256 (``raw_output_sha256``).
    The output itself: ``/raw`` whole, or ``/raw-chunk`` in pieces."""
    return evidence.serialize_evidence(evidence.get_evidence(db, agent.project_id, evidence_id))


@router.get("/evidence/{evidence_id}/raw-chunk", summary="A piece of an evidence record's raw output")
def evidence_raw_chunk(
    evidence_id: int = Path(..., gt=0),
    offset: int = Query(0, ge=0, description="Byte offset: 0, then each read's next_offset."),
    length: int = Query(
        evidence.RAW_CHUNK_DEFAULT, ge=1, le=evidence.RAW_CHUNK_MAX,
        description="Bytes to read (a piece ends on a character boundary, so it may be a few short).",
    ),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """``{evidence_id, offset, length, total_bytes, next_offset, sha256,
    text}`` (2.519.0): read from 0 until ``next_offset`` is null; the pieces
    joined are the output, and their SHA-256 is ``sha256``."""
    record = evidence.get_evidence(db, agent.project_id, evidence_id)
    return evidence.read_raw_chunk(record, offset, length)


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------

class _ProposalBase(BaseModel):
    rationale: Optional[str] = Field(None, max_length=10_000, description="Why — what the reviewer should know.")
    evidence_ids: Optional[List[int]] = Field(
        None, max_length=proposals.EVIDENCE_IDS_MAX,
        description="Evidence records that support it (at most 100).",
    )
    agent_model: Optional[str] = _MODEL
    replaces: Optional[List[int]] = Field(
        None, max_length=proposals.REPLACES_MAX,
        description=(
            "Your own PENDING proposals this one replaces (ids from list_proposals): each becomes "
            "superseded, pointing at the new one. Each must be about what this request proposes "
            "(the same finding and section, new finding, observation, endpoint or subnet)."
        ),
    )


class FindingTextProposal(_ProposalBase):
    finding_id: int = Field(..., gt=0)
    fields: Dict[str, str] = Field(
        ..., description=(
            "Report fields to propose: description, impact, recommendation, references, "
            "steps_to_reproduce, evidence_text (the text of the Evidence section), cvss_vector. Markdown. One proposal per field. Each value is the "
            "section's COMPLETE new text as it will read in the client report — accepting replaces "
            "the section with it — never a critique or suggestions (those go in rationale)."
        ),
    )


class FindingProposal(_ProposalBase):
    title: str = Field(..., min_length=1, max_length=500)
    severity: Literal["critical", "high", "medium", "low", "info"]
    host_ids: List[int] = Field(..., min_length=1, max_length=1000)
    status: Literal["open", "confirmed"] = Field("open", description="open (under investigation) or confirmed.")
    report_text: Optional[Dict[str, str]] = Field(
        None, description="Optional report fields: description, impact, recommendation, references, steps_to_reproduce, evidence_text.",
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


class SubnetDetailsItem(BaseModel):
    cidr: str = Field(..., min_length=1, max_length=64, description="A subnet as scope_list_subnets lists it (or a single address declared in scope).")
    description: Optional[str] = Field(
        None, max_length=proposals.SUBNET_DETAIL_LIMITS["description"],
        description="The subnet's description, as the operator wants it to read.",
    )
    site: Optional[str] = Field(
        None, max_length=proposals.SUBNET_DETAIL_LIMITS["site"],
        description="The site it belongs to (e.g. 'London DC'); an existing site's name, or a new one.",
    )


class SubnetDetailsProposal(_ProposalBase):
    subnets: List[SubnetDetailsItem] = Field(
        ..., min_length=1, max_length=proposals.SUBNET_DETAILS_MAX,
        description="One entry per subnet: its cidr and a description, a site, or both (never empty text).",
    )


@router.post("/proposals/finding-text", status_code=201, summary="Propose report text for a finding")
def propose_finding_text(
    body: FindingTextProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    rows = proposals.propose_finding_text(
        db, agent.project_id, _who(db, request, body.agent_model), finding_id=body.finding_id,
        fields=body.fields, rationale=body.rationale, evidence_ids=body.evidence_ids,
        replaces=body.replaces,
    )
    # Built before the commit, while the rows are loaded (after it,
    # serialize_many reads them again together — never per row).
    # The acknowledgement leaves the text out (serialize_created): it is what
    # the caller just sent, and GET /agent/proposals has the rows whole.
    data = {"proposals": proposals.serialize_created(db, rows)}
    db.commit()
    return data


@router.post("/proposals/finding", status_code=201, summary="Propose a new finding")
def propose_finding(
    body: FindingProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    row = proposals.propose_finding(
        db, agent.project_id, _who(db, request, body.agent_model), title=body.title,
        severity=body.severity, host_ids=body.host_ids, status=body.status,
        report_text=body.report_text, rationale=body.rationale, evidence_ids=body.evidence_ids,
        replaces=body.replaces,
    )
    data = proposals.serialize_created(db, [row])[0]  # before the commit expires it (N8)
    db.commit()
    return data


@router.post("/proposals/observation", status_code=201, summary="Propose promoting or dismissing a scanner observation")
def propose_observation(
    body: ObservationProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    row = proposals.propose_observation(
        db, agent.project_id, _who(db, request, body.agent_model),
        vulnerability_id=body.vulnerability_id, action=body.action, scope=body.scope,
        severity=body.severity, summary=body.summary, rationale=body.rationale,
        evidence_ids=body.evidence_ids, replaces=body.replaces,
    )
    data = proposals.serialize_many(db, [row])[0]  # before the commit expires it (N8)
    db.commit()
    return data


@router.post("/proposals/endpoint-status", status_code=201, summary="Propose a finding endpoint's status")
def propose_endpoint_status(
    body: EndpointStatusProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    row = proposals.propose_endpoint_status(
        db, agent.project_id, _who(db, request, body.agent_model), finding_id=body.finding_id,
        finding_host_id=body.finding_host_id, host_status=body.host_status,
        rationale=body.rationale, evidence_ids=body.evidence_ids, replaces=body.replaces,
    )
    data = proposals.serialize_many(db, [row])[0]  # before the commit expires it (N8)
    db.commit()
    return data


@router.post("/proposals/subnet-details", status_code=201, summary="Propose a description and / or site for scope subnets")
def propose_subnet_details(
    body: SubnetDetailsProposal, request: Request,
    agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db),
):
    """One proposal per subnet (2.522.0; prod feedback #76): a person accepts
    or rejects each, or all of them at once on the Proposals page.  All or
    nothing — a CIDR the scope does not declare is a 404, empty text a 422."""
    rows = proposals.propose_subnet_details(
        db, agent.project_id, _who(db, request, body.agent_model),
        items=[item.model_dump() for item in body.subnets], rationale=body.rationale,
        evidence_ids=body.evidence_ids, replaces=body.replaces,
    )
    data = {"proposals": proposals.serialize_many(db, rows)}  # before the commit expires them (N8)
    db.commit()
    return data


@router.get("/proposals", response_model=AgentRowsPage, summary="Proposals in this project and what happened to them")
def list_proposals(
    request: Request,
    status: Optional[Literal["pending", "accepted", "rejected", "superseded"]] = Query(None),
    kind: Optional[Literal[
        "finding_text", "finding_create", "observation_promote", "observation_dismiss", "endpoint_status",
        "subnet_details",
    ]] = Query(None),
    finding_id: Optional[int] = Query(None, gt=0),
    mine: bool = Query(False, description="Only this session's proposals."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session_id = request.state.agent_session_id if mine else None
    rows, total = proposals.list_proposals(
        db, agent.project_id, status=status, kind=kind, finding_id=finding_id,
        agent_session_id=session_id, limit=limit, offset=offset,
    )
    # Without the texts (their lengths instead, 2.519.0): 14 rows were 43.8 KB.
    # One proposal's texts: the route below.
    items = proposals.without_texts(proposals.serialize_many(db, rows))
    return agent_page(items, total=total, limit=limit, offset=offset)


@router.get("/proposals/{proposal_id}", summary="One proposal, its texts included")
def get_proposal(
    proposal_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The whole proposal (2.519.0): what the list leaves out — the proposed
    text (``payload``), the section's text when it was proposed
    (``base_value``) and now (``current_value``).  Another project's is 404."""
    return proposals.serialize_many(db, [proposals.get_proposal(db, agent.project_id, proposal_id)])[0]
