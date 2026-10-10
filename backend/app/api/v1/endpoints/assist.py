"""
Starting an agent session (JWT) — ``POST /projects/{project_id}/assist/start``.

The path keeps its v2.64.0 ``/assist`` name; since v2.337.0 what it starts is
THE agent session: one ``AgentSession`` row and one key that acts with the
operator's own project permissions.  It is the one way an agent is started.

Everything that reads or ends a session is in ``agent_sessions.py``, keyed by
the session id this route returns (``/projects/{id}/agent-sessions/…``).  This
module also listed, showed and ended sessions until v2.449.0 — keyed by the id
of a second row (``assist_sessions``) that each start wrote.  That table is
gone (migration ``b8e2a5c7d1f3``), and so are the by-id paths that still
answered for the old ids (2.489.0, revision ``c2a7e5f1d9b3``).

The agent-facing counterparts (``/agent/assist/*``, X-API-Key auth) live in
``agent_assist.py``; the two surfaces are physically separated — different auth
contracts, different dependency chains, different audit scopes.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services.agent_key_ttl import resolve_ttl_hours
from app.services.agent_prompt_service import build_session_instructions, resolve_base_url
from app.services.agent_session_service import (
    create_agent_session,
    mint_session_key,
    resolve_project_agent,
)
from app.services.mcp_client_setup_service import McpClientSetup, build_session_mcp_clients

router = APIRouter()

# Key TTL: the deployment default (``AGENT_KEY_TTL_HOURS``) — v2.338.0 retired
# the 4h assist-only default, which dated from read-only assist keys.  The
# dialog reads the resolved value from the response (``key_ttl_hours``), so
# there is no literal to keep in step.


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class StartAssistRequest(BaseModel):
    """Body for POST /projects/{id}/assist/start."""

    purpose: Optional[str] = Field(
        default=None,
        max_length=400,
        description=(
            "A short label for the session, shown wherever sessions are "
            "listed. The app's own dialog does not ask for one; a script "
            "that starts sessions may label them with it. It is not put "
            "in the agent's prompt."
        ),
    )
    ttl_hours: Optional[int] = Field(
        default=None,
        ge=1,
        le=24,
        description=(
            "Override the deployment's default key TTL (AGENT_KEY_TTL_HOURS). "
            "Cannot exceed 24h; the key can be renewed, and the session resumed, "
            "within the session's lifetime."
        ),
    )
    # A key this route does not take is REFUSED (422), never ignored.  A
    # session acts with its operator's project role, re-checked on every call;
    # there is nothing a caller can ask for here that narrows it.  So a body
    # that tries — the ``can_write_assigned: false`` of the capability system
    # removed in v2.309.0 asked for a read-only key — must not get a 201 and a
    # key with the operator's full authority, which is what pydantic's default
    # ``extra="ignore"`` would hand it.  (Until 2.489.0 that one name was kept
    # as a field whose only job was to be refused; forbidding every unknown
    # key says the same for it and for anything else.)
    model_config = ConfigDict(extra="forbid")


# --- MCP client setup -------------------------------------------------------
# The recipes live in ``services/mcp_client_setup_service.py`` (v2.279.0): one
# builder for the start and the resume response, so a fix to a client recipe
# lands on both.


class StartAssistResponse(BaseModel):
    # The session's id — the one the agent reports, Agent Sessions lists and
    # every ``/agent-sessions/{id}`` route takes.  It is the session's ONLY id
    # (v2.449.0).
    agent_session_id: int
    project_id: int
    project_name: str
    agent_id: int
    api_key: str
    instructions: str
    # Per-client MCP setup, in the shape each host actually reads — see
    # McpClientSetup.  The lower-friction alternative to the curl recipe.
    mcp_clients: List[McpClientSetup] = []
    mcp_url: str
    # v2.309.0 — `capabilities` / `capability_constraint` removed. The session
    # can do what its operator can do, so the dialog states the operator's role
    # rather than echoing a grant back at them.
    # v2.65.0 — surface the resolved TTL so the dialog can render
    # the actual expiry without hardcoding a value that drifts when
    # AGENT_KEY_TTL_HOURS changes.  `resolve_ttl_hours()` already applies
    # the global cap so this value reflects what the key was minted with.
    key_ttl_hours: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/start",
    response_model=StartAssistResponse,
    status_code=201,
    summary="Start an agent session (mints its key)",
)
def start_assist_session(
    body: StartAssistRequest,
    request: Request,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    # v2.308.0 — AUDITOR, not ANALYST. "Auditors get a read-only agent" was a
    # settled decision that nothing implemented: every session-start endpoint
    # still required analyst, so the auditor path was theory. An auditor's key
    # carries the auditor's own permissions on every call
    # (see enforce_agent_operator_access), so it cannot write project data or
    # pull a bulk export it would be refused in the UI.
    #
    # Writes (uploads, host tests, evidence, notes) are ANALYST at each endpoint.
    current_user: User = Depends(require_project_role(ProjectRole.AUDITOR)),
):
    """Start the operator's project agent session and mint its key — the one
    way an agent is started (v2.433.0).  The key acts with the starting
    operator's own project permissions on every call, re-checked per request:
    an auditor's agent can read but not write, because the auditor cannot.
    The plaintext key is shown exactly once, in the instructions block.

    Writes ONE row, the ``AgentSession`` (v2.449.0), and its key.

    Role gate: AUDITOR (v2.308.0) to start; writes (uploads, host tests,
    evidence, notes) need ANALYST at the endpoint.
    """
    agent = resolve_project_agent(db, project_id=project.id, user=current_user)
    session = create_agent_session(
        db, project_id=project.id, agent_id=agent.id,
        started_by_id=current_user.id, purpose=body.purpose,
    )
    # v2.338.0 — the deployment default TTL unless the caller names one.
    raw_key = mint_session_key(
        db, agent=agent, session=session, ttl_hours=body.ttl_hours,
    )
    instructions = build_session_instructions(
        request=request,
        session_id=session.id,
        project_id=project.id,
        project_name=project.name,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
    )
    # Read before commit() expires the row (a SELECT per attribute after it).
    session_id = session.id
    agent_id = agent.id
    db.commit()

    mcp_url = f"{resolve_base_url(request)}/mcp"
    mcp_clients = build_session_mcp_clients(
        mcp_url, raw_key,
        project_name=project.name,
        agent_session_id=session_id,
    )
    return StartAssistResponse(
        agent_session_id=session_id,
        project_id=project.id,
        project_name=project.name,
        agent_id=agent_id,
        api_key=raw_key,
        instructions=instructions,
        mcp_clients=mcp_clients,
        mcp_url=mcp_url,
        key_ttl_hours=resolve_ttl_hours(body.ttl_hours),
    )
