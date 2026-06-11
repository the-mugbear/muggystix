"""
JWT-facing endpoints for the agent-assist workflow (v2.64.0).

Mounted under ``/projects/{project_id}/assist/*``.  The agent-facing
counterparts (``/agent/assist/*``, X-API-Key auth) live in
``agent_assist.py``; the two surfaces are physically separated for
the same reason the recon and plan surfaces are — different auth
contracts, different dependency chains, different audit scopes.

Endpoints here let an authenticated operator:

* Start an assist session (returns a fresh API key + agent prompt).
* End an active session (revokes the key; session row stays for
  audit history).
* List the project's recent assist sessions.

No "resume" affordance in v1: an assist session ending is cheap
(the operator just starts another one) and the absence of resume
keeps the user-facing surface small.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.db.models_agent import Agent, AgentSessionWorkflow, AssistSession, AssistSessionStatus
from app.db.models_auth import APIKey, User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services.agent_key_ttl import resolve_expires_at, resolve_ttl_hours
from app.services.agent_session_service import create_agent_session
from app.services.agent_prompt_service import build_assist_instructions

router = APIRouter()


# Assist keys are issued with a deliberately shorter TTL than the
# default agent-key (24h).  Assist sessions are conversational; an
# operator who hasn't pinged the API in 4h has either finished or
# moved on, and a hanging key from yesterday is just an orphan.
#
# NOTE — three-place lockstep:
#   1. This constant (the authoritative value the API enforces).
#   2. ``StartAssistDialog.tsx`` mentions "4 h TTL" in its description
#      and acknowledgement copy.  A future change here must bump that
#      too — search the dialog for "TTL" before merging.
#   3. ``build_assist_instructions`` in agent_prompt_service surfaces
#      the TTL in the agent prompt indirectly via response.expires_at;
#      no literal there to bump.
ASSIST_KEY_DEFAULT_TTL_HOURS = 4


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class StartAssistRequest(BaseModel):
    """Body for POST /projects/{id}/assist/start."""

    purpose: Optional[str] = Field(
        default=None,
        max_length=400,
        description=(
            "Short free-text description of what the operator is doing. "
            "Surfaced on the audit timeline so a reviewer can see why "
            "the session was opened (e.g. 'Looking for FTP exposure', "
            "'Writing critical-findings summary')."
        ),
    )
    ttl_hours: Optional[int] = Field(
        default=None,
        ge=1,
        le=24,
        description=(
            "Override the default 4-hour key TTL.  Cannot exceed 24h; "
            "longer-lived agent work belongs in the recon/plan/execute "
            "workflows that have proper session resume."
        ),
    )


class StartAssistResponse(BaseModel):
    assist_session_id: int
    project_id: int
    project_name: str
    agent_id: int
    api_key: str
    instructions: str
    # v2.65.0 — surface the resolved TTL so the dialog can render
    # the actual expiry without hardcoding a value that drifts when
    # AGENT_KEY_TTL_HOURS / ASSIST_KEY_DEFAULT_TTL_HOURS change.
    # `resolve_ttl_hours()` already applies the global cap so this
    # value reflects what the key was actually minted with.
    key_ttl_hours: int


class AssistSessionRow(BaseModel):
    id: int
    project_id: int
    purpose: Optional[str]
    status: str
    started_by_id: Optional[int]
    started_by_username: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    last_activity_at: Optional[datetime]
    environment_probed: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_assist_agent(db: Session, *, project: Project, user: User) -> Agent:
    """Return the per-user, per-project Agent row for assist sessions.

    Shares the existing Agent row used by recon (one ``{user}-agent``
    per (user, project)).  Auto-provisions if missing.  This keeps
    ``Agent.last_activity_at`` honest across workflows — the SAME
    agent identity drives all four surfaces, just with different
    scoped keys.
    """
    agent = (
        db.query(Agent)
        .filter(Agent.project_id == project.id, Agent.owner_id == user.id)
        .first()
    )
    if agent is not None:
        if not agent.is_active:
            agent.is_active = True
        return agent
    agent = Agent(
        name=f"{user.username}-agent",
        project_id=project.id,
        owner_id=user.id,
        description="Auto-provisioned for agentic workflows",
    )
    db.add(agent)
    db.flush()
    return agent


def _mint_assist_session_key(
    db: Session,
    *,
    agent: Agent,
    assist_session: AssistSession,
    ttl_hours: Optional[int],
    agent_session_id: Optional[int] = None,
) -> str:
    """Mint a fresh assist-session-pinned API key; return the plaintext.

    Revokes any prior active key bound to *this* session first.  Same
    invariant as the recon variant: one live key per session, ever.
    Keys for OTHER assist sessions are untouched, so concurrent
    assists (e.g. two operators on the same project) stay isolated.
    """
    db.query(APIKey).filter(
        APIKey.assist_session_id == assist_session.id,
        APIKey.is_active.is_(True),
    ).update({"is_active": False}, synchronize_session=False)

    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    db.add(
        APIKey(
            agent_id=agent.id,
            assist_session_id=assist_session.id,
            agent_session_id=agent_session_id,
            name=f"assist-session-{assist_session.id}",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=raw_key[:14],
            expires_at=resolve_expires_at(ttl_hours or ASSIST_KEY_DEFAULT_TTL_HOURS),
        )
    )
    return raw_key


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/start",
    response_model=StartAssistResponse,
    status_code=201,
    summary="Start an interactive assist session (mints a read-only agent key)",
)
def start_assist_session(
    body: StartAssistRequest,
    request: Request,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Create an AssistSession and mint a project-scoped, read-only
    agent API key.  The key grants access to ``/agent/assist/*`` only;
    test plan, recon, and execution endpoints all reject assist keys
    with 403.  The plaintext key is shown exactly once — copy it to
    the agent prompt the response contains.

    Role gate: ANALYST (same level as recon/plan-start).  Viewers and
    auditors cannot mint assist keys because the key authenticates as
    the operator's project agent and that has more authority than the
    viewer role implies — even though the assist surface itself is
    read-only.
    """
    agent = _resolve_assist_agent(db, project=project, user=current_user)

    # Unified base session first, so the detail row + key both link to it
    # (R5 — expand-phase completion; was left null for the backfill).
    base_session = create_agent_session(
        db,
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=current_user.id,
        status=AssistSessionStatus.ACTIVE.value,
    )

    assist_session = AssistSession(
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=current_user.id,
        status=AssistSessionStatus.ACTIVE.value,
        purpose=body.purpose,
        agent_session_id=base_session.id,
    )
    db.add(assist_session)
    db.flush()

    raw_key = _mint_assist_session_key(
        db,
        agent=agent,
        assist_session=assist_session,
        ttl_hours=body.ttl_hours,
        agent_session_id=base_session.id,
    )
    instructions = build_assist_instructions(
        request=request,
        assist_session_id=assist_session.id,
        project_id=project.id,
        project_name=project.name,
        purpose=body.purpose,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
    )
    db.commit()
    db.refresh(assist_session)

    return StartAssistResponse(
        assist_session_id=assist_session.id,
        project_id=project.id,
        project_name=project.name,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        key_ttl_hours=resolve_ttl_hours(
            body.ttl_hours or ASSIST_KEY_DEFAULT_TTL_HOURS
        ),
    )


@router.post(
    "/sessions/{session_id}/end",
    status_code=204,
    summary="End an assist session (revokes the key; session row preserved for audit)",
)
def end_assist_session(
    session_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    session = (
        db.query(AssistSession)
        .filter(
            AssistSession.id == session_id,
            AssistSession.project_id == project.id,
        )
        .first()
    )
    if session is None:
        raise HTTPException(
            status_code=404, detail="Assist session not found in this project"
        )
    if session.status != AssistSessionStatus.ACTIVE.value:
        # Idempotent — calling end twice is harmless, but we 200 (well,
        # 204) only on the first call.  Subsequent calls 409 so the
        # caller knows the state didn't change.
        raise HTTPException(
            status_code=409,
            detail=f"Session already in state '{session.status}'.",
        )

    db.query(APIKey).filter(
        APIKey.assist_session_id == session.id,
        APIKey.is_active.is_(True),
    ).update({"is_active": False}, synchronize_session=False)

    session.status = AssistSessionStatus.ENDED.value
    session.ended_at = datetime.now(timezone.utc)
    db.commit()


@router.get(
    "/sessions",
    response_model=List[AssistSessionRow],
    summary="List recent assist sessions in this project",
)
def list_assist_sessions(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.VIEWER)),
):
    """All assist sessions for the project, newest first.  Visible to
    viewers (read-only view of audit metadata; no key material).  Cap
    at 100 — v1 doesn't paginate this list, but most projects will
    have only a handful of recent sessions.
    """
    rows = (
        db.query(AssistSession, User.username)
        .outerjoin(User, AssistSession.started_by_id == User.id)
        .filter(AssistSession.project_id == project.id)
        .order_by(AssistSession.started_at.desc())
        .limit(100)
        .all()
    )
    return [
        AssistSessionRow(
            id=s.id,
            project_id=s.project_id,
            purpose=s.purpose,
            status=s.status,
            started_by_id=s.started_by_id,
            started_by_username=username,
            started_at=s.started_at,
            ended_at=s.ended_at,
            last_activity_at=s.last_activity_at,
            environment_probed=s.environment_probed_at is not None,
        )
        for s, username in rows
    ]
