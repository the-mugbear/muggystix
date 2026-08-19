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
import json
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_project, require_project_role
from app.db.models_agent import (
    ASSIST_GRANTABLE_CAPABILITIES,
    Agent,
    AgentCapabilityConstraint,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
)
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.services.agent_key_ttl import resolve_expires_at, resolve_ttl_hours
from app.services.agent_session_service import create_agent_session
from app.services.agent_prompt_service import build_assist_instructions, resolve_base_url

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
    can_write_assigned: bool = Field(
        default=False,
        description=(
            "Grant the session permission to write host notes and set review "
            "status, limited to hosts assigned to the operator starting it. "
            "Defaults to false — an assist session is read-only unless the "
            "operator opts in.  The grant never widens beyond the operator's "
            "own assigned hosts."
        ),
    )


class McpClientSetup(BaseModel):
    """How one MCP-capable host connects to this session's /api/v1/mcp endpoint.

    v2.269.0 — this used to be a single `mcp_config` string in VS Code's shape,
    handed to operators on VS Code, Claude Code, AND Cursor alike.  The clients
    do not agree: VS Code's `.vscode/mcp.json` wraps servers under `servers`,
    while Claude Code and Cursor use `mcpServers` — so two of the three named
    hosts silently ignored the server the dialog told the operator to paste.
    The file path differs per client too, which is why `path` is part of the
    payload rather than something the dialog hardcodes.
    """

    id: str
    # Client name as the operator knows it, for the dialog's tab.
    label: str
    # "file"    -> `payload` is JSON to write at `path`
    # "command" -> `payload` is a shell command to run; `path` is empty
    kind: str
    path: str
    payload: str
    # One line under the payload: what to do with it.
    hint: str


# --- MCP client setup -------------------------------------------------------
# The wrapper key differs by host and the file path differs by host, so a single
# blob can't serve all three.  Claude Code gets its CLI instead of a file: `claude
# mcp add` writes the entry itself, which is fewer steps than editing JSON and
# can't be pasted into the wrong place.
_MCP_SERVER_NAME = "bluestick-assist"
# Codex reads the credential from the environment instead of the config file,
# which is the one client where the key never has to touch disk in plaintext.
_MCP_KEY_ENV_VAR = "BLUESTICK_ASSIST_KEY"


def _mcp_server_entry(mcp_url: str, raw_key: str) -> dict:
    return {
        "type": "http",
        "url": mcp_url,
        "headers": {"X-API-Key": raw_key},
    }


# Deployments default to a self-signed certificate, and every MCP client here is
# Node/Electron — which rejects it with DEPTH_ZERO_SELF_SIGNED_CERT before any
# request is made.  Verified live; without this the connection just fails and the
# error names TLS, not the config, so operators debug the wrong thing.
_TLS_NOTE = (
    "Self-signed cert? Export NODE_TLS_REJECT_UNAUTHORIZED=0 in the shell you launch "
    "the client from, or trust the deployment's certificate first."
)


def _build_mcp_clients(mcp_url: str, raw_key: str) -> List["McpClientSetup"]:
    entry = {_MCP_SERVER_NAME: _mcp_server_entry(mcp_url, raw_key)}
    return [
        McpClientSetup(
            id="vscode",
            label="VS Code Copilot",
            kind="file",
            path=".vscode/mcp.json",
            payload=json.dumps({"servers": entry}, indent=2),
            hint=(
                "Save as .vscode/mcp.json in your workspace, then start the server from the "
                "Copilot MCP panel. The file holds a live key — keep it out of version control. "
                + _TLS_NOTE
            ),
        ),
        McpClientSetup(
            id="claude_code",
            label="Claude Code",
            kind="command",
            path="",
            payload=(
                f"claude mcp add --transport http {_MCP_SERVER_NAME} {mcp_url} "
                f'--header "X-API-Key: {raw_key}"'
            ),
            hint=(
                "Run in your project directory. -s local keeps the key in your own config; "
                "-s project writes .mcp.json into the repo, so do not use it with a live key. "
                + _TLS_NOTE
            ),
        ),
        McpClientSetup(
            id="codex",
            label="Codex",
            kind="command",
            path="",
            payload=(
                f"read -rs {_MCP_KEY_ENV_VAR} && export {_MCP_KEY_ENV_VAR}   # paste the key, then Enter\n"
                f"codex mcp add {_MCP_SERVER_NAME} --url {mcp_url} "
                f"--bearer-token-env-var {_MCP_KEY_ENV_VAR}"
            ),
            hint=(
                "Codex keeps the key out of config.toml — it reads the env var at run time. "
                "`read -rs` keeps it out of your shell history too; re-run it in each new shell "
                "rather than writing the key into a profile. " + _TLS_NOTE
            ),
        ),
        McpClientSetup(
            id="cursor",
            label="Cursor",
            kind="file",
            path=".cursor/mcp.json",
            payload=json.dumps({"mcpServers": entry}, indent=2),
            hint=(
                "Save as .cursor/mcp.json in your project, or ~/.cursor/mcp.json to keep it out "
                "of the repo entirely. The file holds a live key — do not commit it. " + _TLS_NOTE
            ),
        ),
    ]


class StartAssistResponse(BaseModel):
    assist_session_id: int
    project_id: int
    project_name: str
    agent_id: int
    api_key: str
    instructions: str
    # Per-client MCP setup, in the shape each host actually reads — see
    # McpClientSetup.  The lower-friction alternative to the curl recipe.
    mcp_clients: List[McpClientSetup] = []
    mcp_url: str
    # What the session may do beyond reading, so the dialog can state it back
    # to the operator rather than assuming its own checkbox took effect.
    capabilities: List[str] = []
    capability_constraint: Optional[str] = None
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
    # When the session's agent key stops working — the practical question an
    # operator has ("end it now, or let it lapse?").  Deliberately the KEY's
    # expiry rather than a session field: the session row has no lifetime of
    # its own, and it can outlive its key.  Null means no active key remains,
    # i.e. the session is already dead in practice even though `status` still
    # reads 'active' — that state is worth showing, not hiding.
    #
    # Not derivable client-side from started_at + a hardcoded 4 hours:
    # AGENT_KEY_TTL_HOURS can override the default and per-session ttl_hours
    # is a start parameter, so a computed expiry would quietly be wrong.
    key_expires_at: Optional[datetime] = None
    # Audit: which sessions carried write authority, and how narrowly.
    capabilities: List[str] = []
    capability_constraint: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_project_admin(db: Session, *, user: User, project_id: int) -> bool:
    """True for a global admin, or a member whose project role is admin."""
    if user.role == UserRole.ADMIN:
        return True
    membership = (
        db.query(ProjectMembership)
        .filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
        .first()
    )
    return membership is not None and membership.role == ProjectRole.ADMIN.value


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

    # Capability grant.  Read is implicit; writes are opt-in and always
    # narrowed to the starting operator's own assigned hosts, so an assist
    # agent can annotate the work its operator already owns and nothing else.
    if body.can_write_assigned:
        capabilities = sorted(ASSIST_GRANTABLE_CAPABILITIES)
        constraint = AgentCapabilityConstraint.ASSIGNED.value
    else:
        capabilities = []
        constraint = None

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
    base_session.capabilities = capabilities
    base_session.capability_constraint = constraint

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
        capabilities=capabilities,
    )
    db.commit()
    db.refresh(assist_session)

    # MCP connection details. resolve_base_url returns ".../api/v1"; the MCP
    # transport is mounted at /api/v1/mcp, so the endpoint is base_url + "/mcp".
    mcp_url = f"{resolve_base_url(request)}/mcp"
    mcp_clients = _build_mcp_clients(mcp_url, raw_key)

    return StartAssistResponse(
        assist_session_id=assist_session.id,
        project_id=project.id,
        project_name=project.name,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        mcp_clients=mcp_clients,
        mcp_url=mcp_url,
        capabilities=capabilities,
        capability_constraint=constraint,
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

    # v2.240.4 (review follow-up) — ownership check.
    #
    # This filtered on project only, so ANY project analyst could end any
    # other analyst's session, revoking their key mid-conversation and handing
    # their running agent 401s. With several operators each driving their own
    # agent that is a live foot-gun, not a theoretical one.
    #
    # An operator may always stop their own agent; a project admin may clean up
    # after someone who closed their laptop or left the engagement. Peers may
    # not disrupt each other — they gain nothing from it, since an assist
    # agent's writes already carry its operator's name and an "Agent" badge.
    if session.started_by_id != current_user.id and not _is_project_admin(
        db, user=current_user, project_id=project.id
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "This assist session belongs to another operator. Only its "
                "owner or a project admin can end it."
            ),
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
        .options(joinedload(AssistSession.agent_session))
        .outerjoin(User, AssistSession.started_by_id == User.id)
        .filter(AssistSession.project_id == project.id)
        .order_by(AssistSession.started_at.desc())
        .limit(100)
        .all()
    )

    # Key expiry per session, in one grouped query rather than per row.  MAX
    # because a rotated session can hold more than one active key and the
    # operator cares about when access actually stops.
    expiry_by_session = {}
    session_ids = [s.id for s, _ in rows]
    if session_ids:
        expiry_by_session = {
            sid: exp
            for sid, exp in (
                db.query(APIKey.assist_session_id, func.max(APIKey.expires_at))
                .filter(
                    APIKey.assist_session_id.in_(session_ids),
                    APIKey.is_active.is_(True),
                )
                .group_by(APIKey.assist_session_id)
                .all()
            )
        }

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
            key_expires_at=expiry_by_session.get(s.id),
            capabilities=(s.agent_session.capabilities or []) if s.agent_session else [],
            capability_constraint=(
                s.agent_session.capability_constraint if s.agent_session else None
            ),
        )
        for s, username in rows
    ]
