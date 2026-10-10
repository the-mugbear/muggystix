"""
Agent Feedback Endpoints

Two surfaces:
  1. ``POST /agent/feedback`` (agent-facing, API-key auth) — agents
     file feedback at the moment something gets in their way.
  2. ``GET /feedback``, ``GET /feedback/{id}``, ``PATCH /feedback/{id}``,
     ``GET /feedback/stats`` (admin-facing, JWT) — the developer
     triage queue surfaced in the UI.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Text, func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.host_query_common import escape_like
from app.db.models_agent import (
    Agent, AgentApiCall, AgentFeedback, AgentFeedbackStatus, McpToolCall,
)
from app.db.models_auth import APIKey
from app.db.models_project import Project
from app.schemas.pagination import Paginated
from app.db.models_auth import User, UserRole
from app.api.deps import agent_session_metadata_write, get_current_agent, check_agent_rate_limit
from app.api.deps import get_current_user, require_role
from app.services.agent_session_service import sessions_with_a_page


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AgentFeedbackCreate(BaseModel):
    """What an agent POSTs to ``/agent/feedback`` when something got in its
    way — one line of ``friction_notes`` is a complete submission.

    Every field is optional: a row is wanted even when the agent has only a
    frustration message to leave behind.  Over plain HTTP the model ignores a
    key it does not know (pydantic's default, which this model keeps); the
    MCP tool refuses an argument it does not have, like every other tool
    (``test_feedback_takes_no_source``).
    """
    prompt_version: Optional[str] = None
    # The session is never named in the body: it is the key's
    # (``agent_session_id`` on the row).  ``assist_session_id`` (v2.85.0) went
    # with the ``assist_sessions`` table in v2.449.0.
    overall_rating: Optional[int] = Field(None, ge=1, le=5)
    api_critiques: Optional[List[Dict[str, Any]]] = None
    tool_suggestions: Optional[List[Dict[str, Any]]] = None
    friction_notes: Optional[str] = None
    agent_metrics: Optional[Dict[str, Any]] = None


class AgentFeedbackResponse(BaseModel):
    id: int
    project_id: Optional[int]
    agent_id: Optional[int]
    # v2.428.2 — who and where, so the triage queue can check a claim against
    # the record: the session the feedback came from — its page, with its API
    # calls, is ``/agent-sessions/{agent_session_id}`` — and how many calls it
    # made (filled by the admin list/detail routes).  v2.449.0: the page id
    # IS the session id; ``session_page_id`` and ``assist_session_id`` went.
    # ``session_has_page`` is false for a session no page lists (a recon /
    # plan / execution row from before v2.337.0), so no dead link is offered.
    agent_session_id: Optional[int] = None
    session_has_page: Optional[bool] = None
    session_api_calls: Optional[int] = None
    project_name: Optional[str] = None
    agent_name: Optional[str] = None
    # v2.428.5 — the MCP client the session connected with, as its
    # ``initialize`` named itself (clientInfo), e.g. "claude-code". The agent
    # record's name is reused across sessions (a seeded "planner"), so it
    # says nothing about who tested.
    client_name: Optional[str] = None
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
    by_prompt_version: Dict[str, int]
    avg_rating: Optional[float]
    top_tool_suggestions: List[Dict[str, Any]]
    # v2.428.2 — the counts the page's measures link to.
    with_api_critiques: int = 0
    with_tool_suggestions: int = 0


# ---------------------------------------------------------------------------
# Agent-facing (API-key auth)
# ---------------------------------------------------------------------------


class AgentFeedbackAck(BaseModel):
    """What a submitting agent gets back: that it was stored, and how much.

    Not the submission itself — the agent has just written it, and echoing a
    long entry back spent its context for nothing (the same echo was removed
    from proposal creates in v2.456.0).  Admins read the full row at
    ``GET /feedback/{id}``."""
    id: int
    status: str
    agent_session_id: Optional[int] = None
    created_at: Optional[datetime] = None
    friction_notes_chars: int = 0
    api_critique_count: int = 0
    tool_suggestion_count: int = 0

agent_feedback_router = APIRouter()


@agent_feedback_router.post(
    "/feedback",
    # Feedback is about the session, not project data: a read-only operator's
    # agent files it too.
    dependencies=[Depends(agent_session_metadata_write), Depends(check_agent_rate_limit)],
    response_model=AgentFeedbackAck,
    status_code=201,
    summary="Submit structured agent feedback (agent-facing)",
)
def submit_agent_feedback(
    body: AgentFeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
):
    """File feedback — at the moment of friction, as often as it happens.

    Answers a short acknowledgement (the row's id and what it counted), not
    the submission.  The row is stamped with ``agent_id`` and ``project_id`` from the
    authenticated API key — the payload itself cannot override those.
    """
    # The row is stamped with the session id from the key: that is the whole
    # attribution.  (Until v2.442.0 the body could also name a test plan or an
    # execution run, validated here; both are gone.)
    agent_session_id = getattr(request.state, "agent_session_id", None)

    row = AgentFeedback(
        project_id=agent.project_id,
        agent_id=agent.id,
        agent_session_id=agent_session_id,
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
    return AgentFeedbackAck(
        id=row.id,
        status=row.status,
        agent_session_id=row.agent_session_id,
        created_at=row.created_at,
        friction_notes_chars=len(row.friction_notes or ""),
        api_critique_count=len(row.api_critiques or []),
        tool_suggestion_count=len(row.tool_suggestions or []),
    )


# ---------------------------------------------------------------------------
# Admin-facing (JWT auth, admin role)
# ---------------------------------------------------------------------------

admin_feedback_router = APIRouter(
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


def _nonempty_json_list(column):
    """A JSON list column holding at least one item.  ``column != []`` has no
    operator on PostgreSQL's ``json`` type — it raised, so the page's "Has API
    critiques" / "Has tool suggestions" filters answered 500 (v2.428.2); the
    stored text is compared instead."""
    return column.isnot(None) & column.cast(Text).notin_(["[]", "null"])


def _with_context(db: Session, rows: List[AgentFeedback]) -> List[AgentFeedbackResponse]:
    """Rows plus who/where (v2.428.2): project and agent names, the session's
    page and its API-call count.  Grouped queries — one per kind, never per row."""
    out = [AgentFeedbackResponse.model_validate(r) for r in rows]
    if not rows:
        return out
    project_ids = {r.project_id for r in rows if r.project_id}
    agent_ids = {r.agent_id for r in rows if r.agent_id}
    session_ids = {r.agent_session_id for r in rows if r.agent_session_id}
    projects = dict(db.query(Project.id, Project.name).filter(Project.id.in_(project_ids)).all()) if project_ids else {}
    agents = dict(db.query(Agent.id, Agent.name).filter(Agent.id.in_(agent_ids)).all()) if agent_ids else {}
    calls: Dict[int, int] = {}
    clients: Dict[int, str] = {}
    paged = sessions_with_a_page(db, session_ids)
    if session_ids:
        calls = dict(
            db.query(AgentApiCall.agent_session_id, func.count(AgentApiCall.id))
            .filter(AgentApiCall.agent_session_id.in_(session_ids))
            .group_by(AgentApiCall.agent_session_id).all()
        )
        # The MCP log keeps a longer prefix than the key row does, so match on
        # "starts with"; newest initialize wins.
        for sid, name in (
            db.query(APIKey.agent_session_id, McpToolCall.client_name)
            .join(McpToolCall, McpToolCall.api_key_prefix.startswith(APIKey.key_prefix))
            .filter(
                APIKey.agent_session_id.in_(session_ids),
                McpToolCall.rpc_method == "initialize",
                McpToolCall.client_name.isnot(None),
            )
            .order_by(McpToolCall.id.desc()).all()
        ):
            clients.setdefault(sid, name)
    for item in out:
        item.project_name = projects.get(item.project_id)
        item.agent_name = agents.get(item.agent_id)
        if item.agent_session_id:
            item.session_has_page = item.agent_session_id in paged
            item.session_api_calls = int(calls.get(item.agent_session_id, 0))
            item.client_name = clients.get(item.agent_session_id)
    return out


@admin_feedback_router.get(
    "/",
    response_model=Paginated[AgentFeedbackResponse],
    summary="List agent feedback entries",
)
def list_feedback(
    status: Optional[str] = Query(None),
    min_rating: Optional[int] = Query(None, ge=1, le=5),
    has_tool_suggestions: Optional[bool] = Query(None),
    has_api_critiques: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Substring match in friction_notes"),
    project_id: Optional[int] = Query(None, gt=0, description="Feedback from one project."),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(AgentFeedback)
    if project_id is not None:
        q = q.filter(AgentFeedback.project_id == project_id)
    if status:
        q = q.filter(AgentFeedback.status == status)
    if min_rating is not None:
        q = q.filter(AgentFeedback.overall_rating >= min_rating)
    if search:
        q = q.filter(AgentFeedback.friction_notes.ilike(f"%{escape_like(search)}%", escape="\\"))
    # JSON array non-empty filters.  SQLAlchemy's JSON type doesn't give
    # us a portable length check, but ``!= []`` + ``is not None`` gets
    # us close on both postgres and sqlite for the triage use case.
    if has_tool_suggestions:
        q = q.filter(_nonempty_json_list(AgentFeedback.tool_suggestions))
    if has_api_critiques:
        q = q.filter(_nonempty_json_list(AgentFeedback.api_critiques))
    # v2.428.2 — the standard Paginated envelope (was a bare array): the page
    # says "N of M shown" and pages with has_more.
    total = q.count()
    q = q.order_by(AgentFeedback.created_at.desc(), AgentFeedback.id.desc())
    return Paginated[AgentFeedbackResponse].build(
        items=_with_context(db, q.offset(skip).limit(limit).all()),
        total=total, skip=skip, limit=limit,
    )


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

    with_api_critiques = (
        db.query(func.count(AgentFeedback.id))
        .filter(_nonempty_json_list(AgentFeedback.api_critiques))
        .scalar() or 0
    )
    with_tool_suggestions = (
        db.query(func.count(AgentFeedback.id))
        .filter(_nonempty_json_list(AgentFeedback.tool_suggestions))
        .scalar() or 0
    )

    return FeedbackStatsResponse(
        with_api_critiques=int(with_api_critiques),
        with_tool_suggestions=int(with_tool_suggestions),
        total=total,
        by_status=by_status,
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
    return _with_context(db, [row])[0]


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
    return _with_context(db, [row])[0]
