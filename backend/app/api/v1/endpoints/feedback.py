"""
Agent Feedback Endpoints

Two surfaces:
  1. ``POST /agent/feedback`` (agent-facing, API-key auth) — agents
     submit structured feedback at the end of each prompt workflow.
  2. ``GET /feedback``, ``GET /feedback/{id}``, ``PATCH /feedback/{id}``,
     ``GET /feedback/stats`` (admin-facing, JWT) — the developer
     triage queue surfaced in the UI.

Feedback is also persisted by ``bundle_import_service.py`` when an
imported results file contains a top-level ``feedback`` object; that
path doesn't go through these endpoints but produces the same rows.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_agent import (
    Agent, AgentFeedback, AgentFeedbackSource, AgentFeedbackStatus,
    AssistSession, ExecutionSession, ReconSession, TestPlan,
)
from app.db.models_auth import User, UserRole
from app.api.deps import get_current_agent, check_agent_rate_limit
from app.api.v1.endpoints.auth import get_current_user, require_role


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AgentFeedbackCreate(BaseModel):
    """Payload an agent POSTs to ``/agent/feedback`` at the end of a prompt.

    All fields are optional *except* ``source`` — we still want a row
    even if the agent only has a frustration message to leave behind.
    """
    source: str = Field(
        ...,
        description=(
            "One of plan_generation | reconnaissance | in_session_execution "
            "| exported_execution | assist"
        ),
    )
    prompt_version: Optional[str] = None
    test_plan_id: Optional[int] = None
    execution_session_id: Optional[int] = None
    # v2.85.0 — recon/assist linkage.  Pre-v2.85.0 the recon prompt
    # already passed recon_session_id/scope_id but the schema only
    # declared the plan + execution fields, so Pydantic silently
    # dropped them.  Feedback rows from recon and assist sessions
    # now carry their session id forward so the triage queue can
    # filter by workflow.
    recon_session_id: Optional[int] = None
    assist_session_id: Optional[int] = None
    overall_rating: Optional[int] = Field(None, ge=1, le=5)
    api_critiques: Optional[List[Dict[str, Any]]] = None
    tool_suggestions: Optional[List[Dict[str, Any]]] = None
    friction_notes: Optional[str] = None
    agent_metrics: Optional[Dict[str, Any]] = None


class AgentFeedbackResponse(BaseModel):
    id: int
    project_id: Optional[int]
    agent_id: Optional[int]
    test_plan_id: Optional[int]
    execution_session_id: Optional[int]
    recon_session_id: Optional[int] = None
    assist_session_id: Optional[int] = None
    source: str
    prompt_version: Optional[str]
    overall_rating: Optional[int]
    api_critiques: Optional[List[Dict[str, Any]]] = None
    tool_suggestions: Optional[List[Dict[str, Any]]] = None
    friction_notes: Optional[str]
    agent_metrics: Optional[Dict[str, Any]] = None
    status: str
    reviewed_by_id: Optional[int]
    reviewed_at: Optional[datetime]
    reviewer_notes: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentFeedbackUpdate(BaseModel):
    status: Optional[str] = Field(
        None,
        description="One of new | reviewed | actioned | dismissed",
    )
    reviewer_notes: Optional[str] = None


class FeedbackStatsResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_source: Dict[str, int]
    by_prompt_version: Dict[str, int]
    avg_rating: Optional[float]
    top_tool_suggestions: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Agent-facing (API-key auth)
# ---------------------------------------------------------------------------

agent_feedback_router = APIRouter()


@agent_feedback_router.post(
    "/feedback",
    response_model=AgentFeedbackResponse,
    status_code=201,
    summary="Submit structured agent feedback (agent-facing)",
    dependencies=[Depends(check_agent_rate_limit)],
)
def submit_agent_feedback(
    body: AgentFeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """Agent POSTs feedback after finishing a prompt workflow.

    The row is stamped with ``agent_id`` and ``project_id`` from the
    authenticated API key — the payload itself cannot override those.
    If the payload references a test plan or execution session, those
    IDs are validated against the agent's project before persisting.
    """
    if body.source not in {s.value for s in AgentFeedbackSource}:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source {body.source!r}. Allowed: "
                   f"{sorted(s.value for s in AgentFeedbackSource)}",
        )
    # ``exported_execution`` rows are written by bundle_import_service
    # when an imported results file carries a top-level ``feedback``
    # block; the live agent endpoint never produces one.  Reject up
    # front so an agent can't masquerade as a bundle import.
    if body.source == AgentFeedbackSource.EXPORTED_EXECUTION.value:
        raise HTTPException(
            status_code=400,
            detail="source=exported_execution is reserved for bundle import, not live feedback",
        )

    scoped_plan_id = getattr(request.state, "scoped_plan_id", None)
    scoped_scope_id = getattr(request.state, "scoped_scope_id", None)
    scoped_recon_session_id = getattr(request.state, "scoped_recon_session_id", None)
    scoped_assist_session_id = getattr(request.state, "scoped_assist_session_id", None)

    # Source ↔ ID coherence guard (v2.85.2).  Runs for EVERY agent,
    # scoped or legacy/unscoped: the body's source string and the
    # populated session IDs must agree on which workflow this feedback
    # belongs to.  Without this, an unscoped key could write a row with
    # source="plan_generation" *and* a recon_session_id + an
    # assist_session_id (each individually passes its project-level FK
    # check) — an incoherent attribution that the triage queue can't
    # disentangle.  Plan/execution sources own test_plan_id +
    # execution_session_id; reconnaissance owns recon_session_id;
    # assist owns assist_session_id.
    plan_sources = {AgentFeedbackSource.PLAN_GENERATION.value,
                    AgentFeedbackSource.IN_SESSION_EXECUTION.value}
    if body.source in plan_sources:
        if body.recon_session_id is not None or body.assist_session_id is not None:
            raise HTTPException(
                status_code=400,
                detail=f"source={body.source!r} cannot reference recon/assist sessions",
            )
    elif body.source == AgentFeedbackSource.RECONNAISSANCE.value:
        if (body.test_plan_id is not None
                or body.execution_session_id is not None
                or body.assist_session_id is not None):
            raise HTTPException(
                status_code=400,
                detail="source=reconnaissance cannot reference plan/execution/assist IDs",
            )
    elif body.source == AgentFeedbackSource.ASSIST.value:
        if (body.test_plan_id is not None
                or body.execution_session_id is not None
                or body.recon_session_id is not None):
            raise HTTPException(
                status_code=400,
                detail="source=assist cannot reference plan/execution/recon IDs",
            )

    # Workflow-contract guard (v2.85.1).  Each scoped key flavor owns
    # exactly one workflow; reject foreign-flavor `source` values up
    # front so a recon key bound to session A can't attach feedback to
    # session B (audit-attribution leak), and so a plan key can't
    # masquerade as recon/assist.  Source ↔ ID coherence is already
    # enforced above for every agent; this block adds the scope-pinning
    # layer for scoped keys.
    if scoped_plan_id is not None:
        if body.source not in plan_sources:
            raise HTTPException(
                status_code=403,
                detail=f"Plan-scoped API key cannot submit source={body.source!r}",
            )
    elif scoped_scope_id is not None:
        if body.source != AgentFeedbackSource.RECONNAISSANCE.value:
            raise HTTPException(
                status_code=403,
                detail=f"Recon-scoped API key cannot submit source={body.source!r}",
            )
        # If the key is pinned to a specific recon session (v2.45.0+),
        # the body's recon_session_id (if provided) must match exactly —
        # scope-level coherence isn't enough.
        if (scoped_recon_session_id is not None
                and body.recon_session_id is not None
                and body.recon_session_id != scoped_recon_session_id):
            raise HTTPException(
                status_code=403,
                detail="This API key is scoped to a different recon session",
            )
    elif scoped_assist_session_id is not None:
        if body.source != AgentFeedbackSource.ASSIST.value:
            raise HTTPException(
                status_code=403,
                detail=f"Assist-scoped API key cannot submit source={body.source!r}",
            )
        if (body.assist_session_id is not None
                and body.assist_session_id != scoped_assist_session_id):
            raise HTTPException(
                status_code=403,
                detail="This API key is scoped to a different assist session",
            )

    # Defensive FK validation.  Scoped agent keys (test_plan_id set)
    # can only reference their own plan; global agent keys can reference
    # any plan in their project.
    if body.test_plan_id is not None:
        plan = (
            db.query(TestPlan)
            .filter(
                TestPlan.id == body.test_plan_id,
                TestPlan.project_id == agent.project_id,
            )
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail="test_plan_id not found in this project")
        if scoped_plan_id is not None and scoped_plan_id != body.test_plan_id:
            raise HTTPException(
                status_code=403,
                detail="This API key is scoped to a different test plan",
            )
    if body.recon_session_id is not None:
        recon = (
            db.query(ReconSession)
            .filter(
                ReconSession.id == body.recon_session_id,
                ReconSession.project_id == agent.project_id,
            )
            .first()
        )
        if not recon:
            raise HTTPException(
                status_code=404,
                detail="recon_session_id not found in this project",
            )
        if scoped_scope_id is not None and recon.scope_id != scoped_scope_id:
            raise HTTPException(
                status_code=403,
                detail="This API key is scoped to a different recon scope",
            )
    if body.assist_session_id is not None:
        assist = (
            db.query(AssistSession)
            .filter(
                AssistSession.id == body.assist_session_id,
                AssistSession.project_id == agent.project_id,
            )
            .first()
        )
        if not assist:
            raise HTTPException(
                status_code=404,
                detail="assist_session_id not found in this project",
            )
    if body.execution_session_id is not None:
        # Code review critical #4: previously we fetched the session by
        # ID alone and only checked scoped_plan_id for per-plan keys.
        # Global/unscoped agent keys could attach feedback to an
        # execution session belonging to a different project.  Join
        # through TestPlan so the session is resolved only if its
        # plan belongs to the agent's project.
        sess = (
            db.query(ExecutionSession)
            .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
            .filter(
                ExecutionSession.id == body.execution_session_id,
                TestPlan.project_id == agent.project_id,
            )
            .first()
        )
        if not sess:
            raise HTTPException(status_code=404, detail="execution_session_id not found")
        if sess.test_plan_id and scoped_plan_id is not None and scoped_plan_id != sess.test_plan_id:
            raise HTTPException(
                status_code=403,
                detail="This API key is scoped to a different test plan",
            )

    row = AgentFeedback(
        project_id=agent.project_id,
        agent_id=agent.id,
        test_plan_id=body.test_plan_id,
        execution_session_id=body.execution_session_id,
        recon_session_id=body.recon_session_id,
        assist_session_id=body.assist_session_id,
        source=body.source,
        prompt_version=body.prompt_version,
        overall_rating=body.overall_rating,
        api_critiques=body.api_critiques or [],
        tool_suggestions=body.tool_suggestions or [],
        friction_notes=body.friction_notes,
        agent_metrics=body.agent_metrics or {},
        status=AgentFeedbackStatus.NEW.value,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Admin-facing (JWT auth, admin role)
# ---------------------------------------------------------------------------

admin_feedback_router = APIRouter(
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


@admin_feedback_router.get(
    "/",
    response_model=List[AgentFeedbackResponse],
    summary="List agent feedback entries",
)
def list_feedback(
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    min_rating: Optional[int] = Query(None, ge=1, le=5),
    has_tool_suggestions: Optional[bool] = Query(None),
    has_api_critiques: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Substring match in friction_notes"),
    test_plan_id: Optional[int] = Query(
        None,
        description="Filter to feedback rows attributed to a specific test plan (v2.28.0).",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(AgentFeedback)
    if status:
        q = q.filter(AgentFeedback.status == status)
    if source:
        q = q.filter(AgentFeedback.source == source)
    if min_rating is not None:
        q = q.filter(AgentFeedback.overall_rating >= min_rating)
    if test_plan_id is not None:
        q = q.filter(AgentFeedback.test_plan_id == test_plan_id)
    if search:
        q = q.filter(AgentFeedback.friction_notes.ilike(f"%{search}%"))
    # JSON array non-empty filters.  SQLAlchemy's JSON type doesn't give
    # us a portable length check, but ``!= []`` + ``is not None`` gets
    # us close on both postgres and sqlite for the triage use case.
    if has_tool_suggestions:
        q = q.filter(
            AgentFeedback.tool_suggestions.isnot(None),
            AgentFeedback.tool_suggestions != [],
        )
    if has_api_critiques:
        q = q.filter(
            AgentFeedback.api_critiques.isnot(None),
            AgentFeedback.api_critiques != [],
        )
    q = q.order_by(AgentFeedback.created_at.desc())
    return q.offset(skip).limit(limit).all()


_TOOL_NAME_NON_TOOL_GIVEAWAYS = frozenset({
    "hint", "hints", "suggestion", "suggestions", "grouping",
    "approach", "strategy", "workflow", "pattern", "tip", "tips",
    "idea", "ideas", "note", "notes", "process", "consider", "consideration",
    "use", "using", "should",
})

_TOOL_NAME_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")


def _normalize_tool_name(raw: str) -> str:
    """Strip parenthetical qualifiers and collapse whitespace so
    ``"httpx (official binary)"`` aggregates as ``"httpx"``.

    v2.43.2 — added so an agent who appends "(official Docker fallback)"
    to a real tool name still gets counted under the canonical name
    instead of being filtered out by the length cap below.
    """
    cleaned = _TOOL_NAME_PAREN_RE.sub(" ", raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _looks_like_tool_name(name: str) -> bool:
    """Heuristic gate for the ``tool_suggestions`` aggregation
    (v2.43.2 — fixes the Feedback widget surfacing agent-overshot
    workflow hints like "Representative-host grouping hint" as if
    they were CLI tools).

    Real tool names are short binary identifiers (nmap, masscan,
    rustscan, httpx, eyewitness, burp suite, metasploit framework, …)
    — 1-3 words, no sentence words.  This filter rejects entries that
    look like a free-text hint dropped in the wrong field.
    """
    if not name:
        return False
    if len(name) > 40:
        return False
    words = name.split()
    if len(words) > 3:
        return False
    lower_words = {w.lower().rstrip(":,.;-_") for w in words}
    if lower_words & _TOOL_NAME_NON_TOOL_GIVEAWAYS:
        return False
    return True


@admin_feedback_router.get(
    "/stats",
    response_model=FeedbackStatsResponse,
    summary="Feedback queue KPIs",
)
def feedback_stats(db: Session = Depends(get_db)):
    """Aggregate counts for the developer dashboard header."""
    total = db.query(func.count(AgentFeedback.id)).scalar() or 0

    by_status: Dict[str, int] = {}
    for row in db.query(AgentFeedback.status, func.count(AgentFeedback.id)).group_by(AgentFeedback.status).all():
        by_status[row[0]] = int(row[1])

    by_source: Dict[str, int] = {}
    for row in db.query(AgentFeedback.source, func.count(AgentFeedback.id)).group_by(AgentFeedback.source).all():
        by_source[row[0]] = int(row[1])

    by_version: Dict[str, int] = {}
    for row in db.query(AgentFeedback.prompt_version, func.count(AgentFeedback.id)).group_by(AgentFeedback.prompt_version).all():
        by_version[row[0] or "(unset)"] = int(row[1])

    avg_rating = db.query(func.avg(AgentFeedback.overall_rating)).scalar()
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    # Top tool suggestions — aggregate by name across all rows.  JSON
    # column means we aggregate in Python; acceptable for the triage
    # queue scale (expect O(100s) rows, not millions).
    #
    # v2.43.2 — filter out entries that don't look like a CLI tool name.
    # Agents sometimes overshoot the schema and submit workflow hints
    # ("Representative-host grouping hint", "Use nmap before masscan",
    # etc.) in this field instead of using `friction_notes`.  The
    # whitelist heuristic drops anything that:
    #   * has > 4 internal whitespace runs (real names: "nmap", "burp
    #     suite", "metasploit framework" — all <=2 words; sentences are
    #     longer);
    #   * is longer than 40 chars (legitimate binary names are short);
    #   * contains an obvious "this is a sentence" giveaway word.
    # Parenthetical qualifiers (e.g. "httpx (official binary)") get
    # stripped before length check so the underlying tool still counts.
    counts: Dict[str, Dict[str, Any]] = {}
    for row in db.query(AgentFeedback.tool_suggestions).filter(AgentFeedback.tool_suggestions.isnot(None)).all():
        items = row[0] or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            name = _normalize_tool_name(name)
            if not name or not _looks_like_tool_name(name):
                continue
            bucket = counts.setdefault(name, {"name": name, "count": 0, "categories": set()})
            bucket["count"] += 1
            cat = item.get("category")
            if cat:
                bucket["categories"].add(cat)
    top = sorted(counts.values(), key=lambda x: x["count"], reverse=True)[:10]
    top_payload = [
        {"name": t["name"], "count": t["count"], "categories": sorted(list(t["categories"]))}
        for t in top
    ]

    return FeedbackStatsResponse(
        total=total,
        by_status=by_status,
        by_source=by_source,
        by_prompt_version=by_version,
        avg_rating=avg_rating,
        top_tool_suggestions=top_payload,
    )


@admin_feedback_router.get(
    "/{feedback_id}",
    response_model=AgentFeedbackResponse,
)
def get_feedback(
    feedback_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
):
    row = db.query(AgentFeedback).filter(AgentFeedback.id == feedback_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Feedback entry not found")
    return row


@admin_feedback_router.patch(
    "/{feedback_id}",
    response_model=AgentFeedbackResponse,
    summary="Update feedback triage state",
)
def update_feedback(
    body: AgentFeedbackUpdate,
    feedback_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AgentFeedback).filter(AgentFeedback.id == feedback_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Feedback entry not found")
    if body.status is not None:
        if body.status not in {s.value for s in AgentFeedbackStatus}:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown status {body.status!r}. Allowed: "
                       f"{sorted(s.value for s in AgentFeedbackStatus)}",
            )
        row.status = body.status
        row.reviewed_by_id = current_user.id
        row.reviewed_at = datetime.now(timezone.utc)
    if body.reviewer_notes is not None:
        row.reviewer_notes = body.reviewer_notes
    db.commit()
    db.refresh(row)
    return row
