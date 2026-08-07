"""
Agent Management Endpoints

CRUD operations for AI agents. Agents are project-scoped entities that
authenticate via API key and interact with project data programmatically.
"""

import hashlib
import secrets
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_auth import APIKey
from app.db.models_agent import Agent
from app.db.models_project import Project, ProjectRole
from app.api.deps import get_current_project, is_project_admin, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User
from app.core.security import log_audit_event
from app.services.agent_key_ttl import resolve_expires_at, resolve_ttl_hours

router = APIRouter()


# What makes a key "unscoped" (the long-lived global agent key) rather than
# bound to one workflow session.  Used by BOTH rotate and renew — they must
# agree on the definition, and previously didn't: rotate checked only
# ``test_plan_id`` (so it revoked recon + assist keys) while renew checked
# ``test_plan_id`` + ``scope_id`` (so it renewed the newest ASSIST key,
# silently extending a deliberately 4-hour read-only credential).
#
# ``agent_session_id`` alone is sufficient for every key minted since the
# agent_sessions expand phase; the four legacy columns keep pre-backfill keys
# correctly classified as scoped.  Keep this list in lockstep with the scope
# columns on APIKey — a new workflow that forgets to add its column here
# would have its keys treated as global and silently revoked.
_UNSCOPED_KEY_PREDICATES = (
    APIKey.agent_session_id.is_(None),
    APIKey.test_plan_id.is_(None),
    APIKey.scope_id.is_(None),
    APIKey.recon_session_id.is_(None),
    APIKey.assist_session_id.is_(None),
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AgentCreate(BaseModel):
    name: str = Field(..., max_length=100, min_length=1)
    description: Optional[str] = None
    rate_limit_rpm: int = Field(240, ge=1, le=1200)
    # v2.58.0 — per-key TTL.  None uses the deployment default; values
    # above AGENT_KEY_MAX_TTL_HOURS are clamped down server-side
    # rather than rejected, so callers don't need to know the cap.
    ttl_hours: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Optional API-key TTL in hours.  Defaults to the deployment's "
            "AGENT_KEY_TTL_HOURS setting; capped at AGENT_KEY_MAX_TTL_HOURS."
        ),
    )


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100, min_length=1)
    description: Optional[str] = None
    rate_limit_rpm: Optional[int] = Field(None, ge=1, le=1200)


class AgentResponse(BaseModel):
    id: int
    name: str
    project_id: int
    owner_id: int
    description: Optional[str]
    is_active: bool
    rate_limit_rpm: int
    created_at: datetime
    updated_at: Optional[datetime]
    last_activity_at: Optional[datetime]
    api_key_prefix: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AgentCreateResponse(AgentResponse):
    """Returned only on creation — includes the one-time-visible API key."""
    api_key: str


class AgentKeyRotateResponse(BaseModel):
    api_key: str
    message: str = "New API key generated. The old key has been revoked."


class AgentKeyRotateBody(BaseModel):
    """v2.58.0 — optional body for /rotate-key so callers can request a
    non-default TTL.  Existing callers that POST without a body still
    get the deployment default.
    """
    ttl_hours: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Optional API-key TTL in hours.  Defaults to AGENT_KEY_TTL_HOURS; "
            "capped at AGENT_KEY_MAX_TTL_HOURS."
        ),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/",
    response_model=AgentCreateResponse,
    status_code=201,
    summary="Create an agent for this project",
)
def create_agent(
    body: AgentCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    # v2.90.3 (code review NEW A) — agent creation now requires at
    # least the analyst project role.  Pre-fix any project member,
    # including viewers, could create an unscoped agent key and use
    # it via /agent/test-plans/* to draft and populate plans —
    # bypassing the analyst-role gate on the user-side
    # /test-plans/* endpoints.  Restricting key creation closes that
    # privilege-escalation path; existing agent ownership semantics
    # (owner-or-project-admin can update/rotate) are unchanged.
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    # One agent per user per project
    existing = db.query(Agent).filter(
        Agent.project_id == project.id,
        Agent.owner_id == current_user.id,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="You already have an agent for this project. Delete it first or rotate its key.",
        )

    agent = Agent(
        name=body.name,
        project_id=project.id,
        description=body.description,
        rate_limit_rpm=body.rate_limit_rpm,
        owner_id=current_user.id,
    )
    db.add(agent)
    db.flush()

    # Generate API key
    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key_obj = APIKey(
        agent_id=agent.id,
        name=f"agent-{agent.name}",
        key_hash=key_hash,
        key_prefix=raw_key[:14],
        expires_at=resolve_expires_at(body.ttl_hours),
    )
    db.add(api_key_obj)
    db.commit()
    db.refresh(agent)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="agent_created",
        resource_type="agent",
        resource_id=str(agent.id),
        details={"agent_name": agent.name, "project_id": project.id},
    )

    return AgentCreateResponse(
        id=agent.id,
        name=agent.name,
        project_id=agent.project_id,
        owner_id=agent.owner_id,
        description=agent.description,
        is_active=agent.is_active,
        rate_limit_rpm=agent.rate_limit_rpm,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        last_activity_at=agent.last_activity_at,
        api_key=raw_key,
        api_key_prefix=raw_key[:14],
    )


def _agent_response(agent: Agent, db: Session) -> AgentResponse:
    key = (
        db.query(APIKey)
        .filter(APIKey.agent_id == agent.id, APIKey.is_active.is_(True))
        .first()
    )
    return _agent_response_with_key(agent, key)


def _agent_response_with_key(agent: Agent, key: Optional[APIKey]) -> AgentResponse:
    """Pre-resolved-key variant — used by the batched list_agents path
    (code review NEW H) so a project with N agents doesn't fire N
    separate APIKey lookups."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        project_id=agent.project_id,
        owner_id=agent.owner_id,
        description=agent.description,
        is_active=agent.is_active,
        rate_limit_rpm=agent.rate_limit_rpm,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        last_activity_at=agent.last_activity_at,
        api_key_prefix=key.key_prefix if key else None,
    )


@router.get("/", response_model=List[AgentResponse], summary="List agents for this project")
def list_agents(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _current_user: User = Depends(get_current_user),
):
    agents = db.query(Agent).filter(Agent.project_id == project.id).all()
    # v2.91.1 (code review NEW H) — batch the active-APIKey lookup
    # instead of firing one query per agent.  Pre-fix listing N
    # agents issued N+1 queries; on a project with hundreds of
    # contributors that's the dominant cost of the
    # agent-management page refresh.  One IN(...) query + dict
    # lookup is O(N) total.
    agent_ids = [a.id for a in agents]
    if agent_ids:
        key_rows = (
            db.query(APIKey)
            .filter(
                APIKey.agent_id.in_(agent_ids),
                APIKey.is_active.is_(True),
            )
            .all()
        )
        # If an agent has multiple active keys (rare — rotate
        # creates a new one + deactivates the old one in the same
        # commit, so multi-active is a race), keep the most recent
        # to match the pre-fix .first() ordering.
        keys_by_agent: Dict[int, APIKey] = {}
        for k in key_rows:
            existing = keys_by_agent.get(k.agent_id)
            if existing is None or (
                k.created_at and existing.created_at and k.created_at > existing.created_at
            ):
                keys_by_agent[k.agent_id] = k
    else:
        keys_by_agent = {}
    return [_agent_response_with_key(a, keys_by_agent.get(a.id)) for a in agents]


@router.get("/{agent_id}", response_model=AgentResponse, summary="Get agent details")
def get_agent(
    agent_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _current_user: User = Depends(get_current_user),
):
    agent = db.query(Agent).filter(
        Agent.id == agent_id, Agent.project_id == project.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_response(agent, db)


@router.patch("/{agent_id}", response_model=AgentResponse, summary="Update agent settings")
def update_agent(
    body: AgentUpdate,
    agent_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    agent = db.query(Agent).filter(
        Agent.id == agent_id, Agent.project_id == project.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Only the owner or a project admin can update
    # v2.90.3 (code review NEW G) — was current_user.role != "admin"
    # which only matched GLOBAL admins.  A project-admin who isn't a
    # global admin couldn't manage another member's agent in their
    # own project despite the stated "project admin can override
    # ownership" contract.  is_project_admin handles both tiers
    # (global admin → True; project-admin via ProjectMembership →
    # True; everyone else → False).
    if agent.owner_id != current_user.id and not is_project_admin(
        db, project.id, current_user,
    ):
        raise HTTPException(status_code=403, detail="Not the owner of this agent")

    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.rate_limit_rpm is not None:
        agent.rate_limit_rpm = body.rate_limit_rpm

    db.commit()
    db.refresh(agent)
    return _agent_response(agent, db)


@router.delete("/{agent_id}", status_code=204, summary="Deactivate agent and revoke keys")
def delete_agent(
    agent_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    agent = db.query(Agent).filter(
        Agent.id == agent_id, Agent.project_id == project.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # v2.90.3 (code review NEW G) — was current_user.role != "admin"
    # which only matched GLOBAL admins.  A project-admin who isn't a
    # global admin couldn't manage another member's agent in their
    # own project despite the stated "project admin can override
    # ownership" contract.  is_project_admin handles both tiers
    # (global admin → True; project-admin via ProjectMembership →
    # True; everyone else → False).
    if agent.owner_id != current_user.id and not is_project_admin(
        db, project.id, current_user,
    ):
        raise HTTPException(status_code=403, detail="Not the owner of this agent")

    # Revoke all API keys
    db.query(APIKey).filter(APIKey.agent_id == agent.id).update(
        {"is_active": False}
    )
    agent.is_active = False
    db.commit()

    log_audit_event(
        db,
        user_id=current_user.id,
        action="agent_deactivated",
        resource_type="agent",
        resource_id=str(agent.id),
        details={"agent_name": agent.name, "project_id": project.id},
    )


@router.post(
    "/{agent_id}/rotate-key",
    response_model=AgentKeyRotateResponse,
    summary="Generate a new API key and revoke the old one",
)
def rotate_agent_key(
    agent_id: int = Path(..., gt=0),
    body: Optional[AgentKeyRotateBody] = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    agent = db.query(Agent).filter(
        Agent.id == agent_id,
        Agent.project_id == project.id,
        Agent.is_active.is_(True),
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or inactive")

    # v2.90.3 (code review NEW G) — was current_user.role != "admin"
    # which only matched GLOBAL admins.  A project-admin who isn't a
    # global admin couldn't manage another member's agent in their
    # own project despite the stated "project admin can override
    # ownership" contract.  is_project_admin handles both tiers
    # (global admin → True; project-admin via ProjectMembership →
    # True; everyone else → False).
    if agent.owner_id != current_user.id and not is_project_admin(
        db, project.id, current_user,
    ):
        raise HTTPException(status_code=403, detail="Not the owner of this agent")

    # Revoke existing *unscoped* keys only.  Workflow-scoped keys — per-plan
    # (/test-plans/generate, /execute), per-recon-session, and per-assist-
    # session — must keep working independently: rotating a long-lived global
    # key should never collaterally kill an in-flight session.
    #
    # v2.232.0 — this filter previously checked ONLY ``test_plan_id IS NULL``.
    # All four workflows resolve to the SAME Agent row (one agent per
    # (user, project)), and recon/assist keys leave ``test_plan_id`` NULL, so
    # a rotation silently revoked every live recon and assist key for that
    # user — a hard 401 mid-session, recoverable only by starting a new
    # session.  ``agent_session_id`` is the single reliable discriminator:
    # every workflow-scoped key sets it (see _mint_* in test_plans.py,
    # scopes.py, assist.py) and only the global key minted below leaves it
    # NULL.  The legacy columns are checked too so keys minted before the
    # agent_sessions backfill are still recognised as scoped.
    db.query(APIKey).filter(
        APIKey.agent_id == agent.id,
        APIKey.is_active.is_(True),
        *_UNSCOPED_KEY_PREDICATES,
    ).update({"is_active": False})

    # Create new key
    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key_obj = APIKey(
        agent_id=agent.id,
        name=f"agent-{agent.name}",
        key_hash=key_hash,
        key_prefix=raw_key[:14],
        expires_at=resolve_expires_at(body.ttl_hours if body else None),
    )
    db.add(api_key_obj)
    db.commit()

    log_audit_event(
        db,
        user_id=current_user.id,
        action="agent_key_rotated",
        resource_type="agent",
        resource_id=str(agent.id),
        details={"agent_name": agent.name, "project_id": project.id},
    )

    return AgentKeyRotateResponse(api_key=raw_key)


class AgentKeyRenewBody(BaseModel):
    """Extension request — bumps the existing key's ``expires_at`` by
    ``ttl_hours`` from NOW.  Doesn't rotate the secret (the agent keeps
    using the same token), it just refreshes the wall-clock deadline.
    """
    ttl_hours: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Hours from now to set the new expires_at.  Defaults to "
            "AGENT_KEY_TTL_HOURS; capped at AGENT_KEY_MAX_TTL_HOURS."
        ),
    )


class AgentKeyRenewResponse(BaseModel):
    key_id: int
    expires_at: datetime
    ttl_hours_applied: int
    message: str = "API key expiry extended."


@router.post(
    "/{agent_id}/renew-key",
    response_model=AgentKeyRenewResponse,
    summary="Extend the active API key's expiry without rotating the secret",
)
def renew_agent_key(
    agent_id: int = Path(..., gt=0),
    body: Optional[AgentKeyRenewBody] = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Operator-driven TTL extension for an in-flight engagement.

    Distinct from /rotate-key in that the secret stays the same — the
    agent doesn't need to be re-bootstrapped with a new token, only the
    deadline moves.  Use this when an agent run is mid-way through a
    long engagement and approaching its expiry.

    Only extends the agent's *unscoped* keys (not per-plan / per-scope
    keys), same scoping rule as rotate.  Renew test-plan keys via
    POST /test-plans/{id}/regenerate-key or recon keys via the recon
    surface.
    """
    agent = db.query(Agent).filter(
        Agent.id == agent_id,
        Agent.project_id == project.id,
        Agent.is_active.is_(True),
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or inactive")

    # v2.90.3 (code review NEW G) — was current_user.role != "admin"
    # which only matched GLOBAL admins.  A project-admin who isn't a
    # global admin couldn't manage another member's agent in their
    # own project despite the stated "project admin can override
    # ownership" contract.  is_project_admin handles both tiers
    # (global admin → True; project-admin via ProjectMembership →
    # True; everyone else → False).
    if agent.owner_id != current_user.id and not is_project_admin(
        db, project.id, current_user,
    ):
        raise HTTPException(status_code=403, detail="Not the owner of this agent")

    # v2.232.0 — previously filtered on test_plan_id + scope_id only.  Assist
    # keys set neither (only ``assist_session_id``), and because they're minted
    # per session while the global key is minted once at agent creation,
    # ORDER BY created_at DESC reliably picked the ASSIST key: the operator's
    # global key went un-renewed while a deliberately short-lived read-only
    # credential got extended to the full agent TTL.  Share the definition with
    # rotate so the two can't drift apart again.
    key = (
        db.query(APIKey)
        .filter(
            APIKey.agent_id == agent.id,
            APIKey.is_active.is_(True),
            *_UNSCOPED_KEY_PREDICATES,
        )
        .order_by(APIKey.created_at.desc())
        .first()
    )
    if key is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active unscoped key found.  Use /rotate-key to mint a new "
                "one, or renew per-plan / per-scope keys via their dedicated "
                "endpoints."
            ),
        )

    requested = body.ttl_hours if body else None
    new_expires_at = resolve_expires_at(requested)
    applied_hours = resolve_ttl_hours(requested)
    key.expires_at = new_expires_at
    db.commit()

    log_audit_event(
        db,
        user_id=current_user.id,
        action="agent_key_renewed",
        resource_type="agent",
        resource_id=str(agent.id),
        details={
            "agent_name": agent.name,
            "project_id": project.id,
            "key_id": key.id,
            "ttl_hours_applied": applied_hours,
            "new_expires_at": new_expires_at.isoformat(),
        },
    )

    return AgentKeyRenewResponse(
        key_id=key.id,
        expires_at=new_expires_at,
        ttl_hours_applied=applied_hours,
    )
