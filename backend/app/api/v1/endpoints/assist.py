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

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_project, require_project_role
from app.db.models import Annotation, Host
from app.db.models_agent import (
    Agent,
    AgentApiCall,
    AgentFeedback,
    AssistSession,
    AssistSessionStatus,
)
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.services.agent_key_ttl import resolve_ttl_hours
from app.services.assist_session_service import (
    effective_status,
    has_live_key,
    key_expiry_for_sessions,
)
from app.services.agent_prompt_service import resolve_base_url
from app.services.mcp_client_setup_service import build_mcp_clients

router = APIRouter()

# Assist keys are issued with a deliberately shorter TTL than the
# default agent-key (24h).  Assist sessions are conversational; an
# operator who hasn't pinged the API in 4h has either finished or
# moved on, and a hanging key from yesterday is just an orphan.
#
# Key TTL: the deployment default (``AGENT_KEY_TTL_HOURS``) like every other
# session start — v2.338.0 retired the 4h assist-only default, which dated
# from read-only assist keys.  The dialog reads the resolved value from the
# response (``key_ttl_hours``), so there is no literal to keep in step.


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
    # v2.309.0 removed ``can_write_assigned`` with the capability system: a
    # session's authority is its operator's project role now, decided per
    # request, so there is nothing to opt into.
    #
    # v2.310.0 — but it is kept here to be REJECTED, not ignored. Pydantic's
    # default is `extra="ignore"`, and the failure that produces is the worst
    # shape available: a caller that sent `can_write_assigned: false` asking for
    # a read-only key gets a 201 and a key with their full analyst write
    # authority. Silently granting more than was requested is not a compatible
    # change, however compatible the response looks. Sending `true` is equally
    # wrong in the other direction — it asked for "assigned hosts only" and
    # would now get the whole project.
    #
    # So an old caller gets an error explaining the new model, and a chance to
    # decide whether they still want the session.
    can_write_assigned: Optional[bool] = Field(
        default=None,
        deprecated=True,
        description=(
            "REMOVED in v2.309.0. Rejected rather than ignored, because the "
            "value you sent no longer means what it meant. An assist session "
            "now carries the permissions of the operator who starts it, "
            "checked on every call. Omit this field."
        ),
    )

    @field_validator("can_write_assigned")
    @classmethod
    def _reject_retired_capability_flag(cls, value):
        if value is None:
            return value
        raise ValueError(
            "can_write_assigned was removed in v2.309.0 and is no longer "
            "honoured. An assist session now acts with the permissions of the "
            "operator who starts it, re-checked on every call — there is no "
            "per-session write grant to set. Retry without this field; if you "
            "were relying on it to obtain a read-only key, start the session as "
            "a user whose project role is read-only instead."
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
    # v2.331.0 — the handoff the recipes used to stop short of: how this client
    # shows "connected", the first prompt to give the agent, and what the answer
    # looks like from the session that was actually minted.  See
    # ``mcp_client_setup_service.verify_prompt``.
    verify_check: str = ""
    verify_prompt: str = ""
    verify_expected: str = ""


# --- MCP client setup -------------------------------------------------------
# The recipes moved to ``services/mcp_client_setup_service.py`` in v2.279.0, when
# recon / plan / execution sessions started emitting them too.  One builder means
# a fix to a client recipe lands on every workflow at once — the divergence that
# replaces is why two of the three original recipes silently didn't work.


def _build_mcp_clients(
    mcp_url: str, raw_key: str, *, project_name: str, agent_session_id: int
) -> List["McpClientSetup"]:
    # The label the operator checks the agent's answer against must be the id
    # the agent will actually report — ``session_id`` on /agent/identity is the
    # unified AgentSession id, not this dialog's AssistSession row (v2.338.0).
    return [
        McpClientSetup(**client)
        for client in build_mcp_clients(
            mcp_url,
            raw_key,
            expected={
                "project_name": project_name,
                "session_label": f"agent session #{agent_session_id}",
            },
        )
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
    # v2.309.0 — `capabilities` / `capability_constraint` removed. The session
    # can do what its operator can do, so the dialog states the operator's role
    # rather than echoing a grant back at them.
    # v2.65.0 — surface the resolved TTL so the dialog can render
    # the actual expiry without hardcoding a value that drifts when
    # AGENT_KEY_TTL_HOURS changes.  `resolve_ttl_hours()` already applies
    # the global cap so this value reflects what the key was minted with.
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
    # v2.284.0 — how much the session actually did, so the list answers "which
    # of these is worth opening?" without a round trip per row.  A session that
    # made no calls is the common dead end (key minted, prompt never pasted) and
    # should be visibly distinguishable from one that did the work.
    call_count: int = 0
    note_count: int = 0
    # v2.331.0 — how the agent reached this session, from observed calls:
    #   "none" — no authenticated call yet (key minted, client never connected)
    #   "mcp"  — at least one call arrived through the MCP transport
    #   "curl" — calls arrived, all by direct HTTP (the pasted-prompt path)
    # Replaces the probe-based "not yet connected", which a client can skip
    # and still work, or post via curl and never use MCP.  Not a liveness
    # claim: MCP is request/response, so a past call says the client connected,
    # not that it is still running — pair with last_activity_at for that.
    connection: str = "none"
    first_call_at: Optional[datetime] = None


class AssistSessionNote(BaseModel):
    """A note this session's agent wrote, for the review page.

    Notes are the session's durable output — everything else it did was a read.
    They are attributed to the operator with an agent badge, so "what did the
    agent put my name on" is the question this answers.
    """
    id: int
    host_id: Optional[int] = None
    host_ip: Optional[str] = None
    hostname: Optional[str] = None
    body: str
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class AssistSessionDetail(AssistSessionRow):
    """One session, with the material an operator reviews after the fact."""
    # The operator's machine as the agent saw it — the same probe the prompts
    # mandate, kept because "which host was this run from" is part of the
    # audit answer, not just live context.
    environment: Optional[dict] = None
    environment_probed_at: Optional[datetime] = None
    agent_model: Optional[str] = None
    agent_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    notes: List[AssistSessionNote] = []
    # Feedback the agent left about this session, if it closed the loop.
    feedback_count: int = 0


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
    # v2.308.0 — AUDITOR, not ANALYST. "Auditors get a read-only agent" was a
    # settled decision that nothing implemented: every session-start endpoint
    # still required analyst, so the auditor path was theory. Assist is the
    # right workflow to lower first — it is read-only by default, and an
    # auditor's key now carries the auditor's own permissions on every call
    # (see enforce_agent_operator_access), so it cannot write project data or
    # pull a bulk export it would be refused in the UI.
    #
    # Recon / plan / execution stay at ANALYST: they exist to change project
    # state, which is exactly what an auditor may not do.
    current_user: User = Depends(require_project_role(ProjectRole.AUDITOR)),
):
    """Create an AssistSession and mint a project-scoped, read-only
    agent API key.  The key grants access to ``/agent/assist/*`` only;
    test plan, recon, and execution endpoints all reject assist keys
    with 403.  The plaintext key is shown exactly once — copy it to
    the agent prompt the response contains.

    Role gate: AUDITOR (v2.308.0).  The key acts with the starting operator's
    own project permissions on every call, so an auditor's assist agent is
    read-only because the auditor is — there is no separate grant to get wrong.
    Recon, plan generation and execution remain ANALYST: they exist to change
    project state.
    """
    # v2.337.0 — "AI Assist" mints the same unified PROJECT session as every
    # other entry point; there is no separate assist key any more. The session
    # can query, and (role permitting) go on to recon / plan / execute with the
    # same key.
    from app.services.agent_session_service import (
        create_agent_session, resolve_project_agent, mint_session_key,
    )
    from app.services.agent_prompt_service import build_session_instructions

    agent = resolve_project_agent(db, project_id=project.id, user=current_user)
    base_session = create_agent_session(
        db, project_id=project.id, agent_id=agent.id,
        started_by_id=current_user.id, purpose=body.purpose,
    )
    # An AssistSession detail row is still created, linked to the base
    # session, because the /assist-sessions review page and its end/detail
    # routes are keyed by this table's ids.  It is a pointer, not the record:
    # ``purpose``, ``last_activity_at`` and the probe live on the base row and
    # the page reads them through it (``_session_row``).  Collapsing the page
    # onto ``agent_sessions`` outright would change every id it links on, so
    # that is a separate change.
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
    # v2.338.0 — the deployment default TTL, like every other session start.
    # The 4h assist-only default dated from read-only assist keys; the session
    # this mints is the same kind the other three buttons mint.
    raw_key = mint_session_key(
        db, agent=agent, session=base_session, ttl_hours=body.ttl_hours,
    )
    instructions = build_session_instructions(
        request=request,
        session_id=base_session.id,
        project_id=project.id,
        project_name=project.name,
        purpose=body.purpose,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
    )
    db.commit()
    db.refresh(assist_session)

    mcp_url = f"{resolve_base_url(request)}/mcp"
    mcp_clients = _build_mcp_clients(
        mcp_url, raw_key,
        project_name=project.name,
        agent_session_id=base_session.id,
    )
    return StartAssistResponse(
        assist_session_id=assist_session.id,
        project_id=project.id,
        project_name=project.name,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        mcp_clients=mcp_clients,
        mcp_url=mcp_url,
        key_ttl_hours=resolve_ttl_hours(body.ttl_hours),
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
        APIKey.agent_session_id == session.agent_session_id,
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
    status: Optional[str] = Query(
        None, description="Filter by effective status (active / ended)."
    ),
    mine: bool = Query(False, description="Only sessions this user started."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.VIEWER)),
):
    """All assist sessions for the project, newest first.  Visible to
    viewers (read-only view of audit metadata; no key material).

    v2.284.0 — paginated and filterable, because this now backs a review page
    rather than only the start dialog's "do I already have one running?" panel.
    The `status` filter runs against the EFFECTIVE status (a session whose key
    has expired reads as ended), so it agrees with what the caller is shown
    rather than with a stored value the sweep may not have converged yet.
    """
    # The derived status is filtered in SQL rather than after the fetch, so the
    # filter and the pagination agree.  An earlier shape took the newest 500,
    # derived in Python, then sliced — past 500 sessions an older `ended` one was
    # unreachable, silently.
    #
    # The filter is a correlated EXISTS, not a grouped subquery: an aggregate
    # over api_keys has no access to this query's project or page, so it scaled
    # with the deployment's whole key history on every request.  The expiry we
    # *display* is fetched for the page's ids only, below.
    now = datetime.now(timezone.utc)
    live_key = has_live_key(now)
    stored_active = AssistSession.status == AssistSessionStatus.ACTIVE.value

    q = (
        db.query(AssistSession, User.username)
        .options(joinedload(AssistSession.agent_session))
        .outerjoin(User, AssistSession.started_by_id == User.id)
        .filter(AssistSession.project_id == project.id)
    )
    if mine:
        q = q.filter(AssistSession.started_by_id == current_user.id)
    if status == AssistSessionStatus.ACTIVE.value:
        q = q.filter(stored_active, live_key)
    elif status:
        # Everything not effectively active — including a row still stored as
        # `active` whose key has died and the sweep hasn't caught yet.
        q = q.filter(~(stored_active & live_key))
        if status != AssistSessionStatus.ENDED.value:
            # A specific non-active status still filters on the stored value;
            # only `active`/`ended` are derived.
            q = q.filter(AssistSession.status == status)

    rows = (
        q.order_by(AssistSession.started_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    session_ids = [s.id for s, _ in rows]
    expiry_by_session = key_expiry_for_sessions(db, session_ids)
    activity_by_session = _session_activity(db, session_ids)
    notes_by_session = _note_counts(db, [s for s, _ in rows])

    return [
        _session_row(
            s,
            username,
            expiry_by_session.get(s.id),
            now=now,
            note_count=notes_by_session.get(s.id, 0),
            activity=activity_by_session.get(s.id),
        )
        for s, username in rows
    ]


def _latest(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    """The later of two optional timestamps (tz-naive values read as UTC)."""
    def _aware(t):
        return t if t is None or t.tzinfo is not None else t.replace(tzinfo=timezone.utc)
    a, b = _aware(a), _aware(b)
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _probe_source(session: AssistSession):
    """The row whose environment probe to show: the unified session when it
    has one, else this legacy detail row."""
    base = session.agent_session
    if base is not None and base.environment_probed_at is not None:
        return base
    return session


def _session_row(
    session: AssistSession,
    username: Optional[str],
    key_expires_at,
    *,
    now,
    note_count: int = 0,
    activity: Optional["_SessionActivity"] = None,
) -> AssistSessionRow:
    """Map one session to its wire row.

    Shared by the list and the detail endpoint (which extends this shape).  The
    two built the same 18 fields independently, so adding one meant remembering
    both — and the one you forget is the one that silently reads as its default.
    """
    # v2.338.0 — read the live columns through the unified AgentSession.  The
    # audit middleware refreshes ``agent_sessions.last_activity_at`` and the
    # probe writes ``agent_sessions.environment*``; nothing writes those
    # columns on this detail row any more, so reading them here reported
    # every post-consolidation session as idle-forever and never-probed.
    # Sessions from before the consolidation carry the values on this row
    # and have no live base row worth preferring.
    base = session.agent_session
    activity = activity or _SessionActivity()
    stored_status = session.status
    ended_at = session.ended_at
    if base is not None and base.status != "active":
        # The base row is what the key checks and what a supersede/lapse
        # ends; mirror it so the page never shows a dead session as active.
        stored_status = AssistSessionStatus.ENDED.value
        ended_at = ended_at or base.completed_at
    return AssistSessionRow(
        id=session.id,
        project_id=session.project_id,
        purpose=(base.purpose if base is not None and base.purpose else session.purpose),
        status=effective_status(stored_status, key_expires_at, now),
        started_by_id=session.started_by_id,
        started_by_username=username,
        started_at=session.started_at,
        ended_at=ended_at,
        last_activity_at=_latest(
            session.last_activity_at,
            base.last_activity_at if base is not None else None,
        ),
        environment_probed=(
            session.environment_probed_at is not None
            or (base is not None and base.environment_probed_at is not None)
        ),
        key_expires_at=key_expires_at,
        call_count=activity.call_count,
        note_count=note_count,
        connection=activity.connection,
        first_call_at=activity.first_call_at,
    )


class _SessionActivity:
    """What the audit log says about one session, in the shape the row needs."""

    __slots__ = ("call_count", "connection", "first_call_at")

    def __init__(
        self,
        call_count: int = 0,
        via_mcp: bool = False,
        first_call_at: Optional[datetime] = None,
    ):
        self.call_count = call_count
        self.first_call_at = first_call_at
        if call_count == 0:
            self.connection = "none"
        else:
            self.connection = "mcp" if via_mcp else "curl"


def _session_activity(db: Session, session_ids: List[int]) -> dict:
    """Audited call count, transport, and first-call time per session, in one
    grouped query.

    The list is the entry point to the review page, so "did this session do
    anything?" has to be answerable without opening each one — a session with
    zero calls is the common dead end (key minted, prompt never pasted) and
    reads identically to a busy one without this.

    v2.331.0 — also whether any call came through MCP.  Every audited row is an
    authenticated call (the middleware writes nothing for a request that never
    authenticated), so one row is proof the key was accepted, and ``via_mcp``
    on any of them is proof the MCP transport carried it.  ``max(case)`` rather
    than ``bool_or`` so the test suite's SQLite runs the same query.
    """
    if not session_ids:
        return {}
    # v2.337.0 — calls attribute to the unified agent_session_id now, so join
    # AgentApiCall → AssistSession on that and group by the AssistSession id
    # the review page keys on.
    mcp_seen = func.max(case((AgentApiCall.via_mcp.is_(True), 1), else_=0))
    return {
        sid: _SessionActivity(
            call_count=count, via_mcp=bool(via_mcp), first_call_at=first_at
        )
        for sid, count, via_mcp, first_at in (
            db.query(
                AssistSession.id,
                func.count(AgentApiCall.id),
                mcp_seen,
                func.min(AgentApiCall.created_at),
            )
            .join(AgentApiCall, AgentApiCall.agent_session_id == AssistSession.agent_session_id)
            .filter(AssistSession.id.in_(session_ids))
            .group_by(AssistSession.id)
            .all()
        )
    }


def _note_counts(db: Session, sessions: List[AssistSession]) -> dict:
    """Notes written per assist session.

    Annotations hang off the unified ``AgentSession``, not the assist row, so
    this maps back through ``agent_session_id`` — a session started before that
    binding existed simply has none to find.
    """
    agent_session_ids = {
        s.agent_session_id: s.id for s in sessions if s.agent_session_id is not None
    }
    if not agent_session_ids:
        return {}
    counts = (
        db.query(Annotation.agent_session_id, func.count(Annotation.id))
        .filter(Annotation.agent_session_id.in_(list(agent_session_ids)))
        .group_by(Annotation.agent_session_id)
        .all()
    )
    return {agent_session_ids[asid]: count for asid, count in counts}


@router.get(
    "/sessions/{session_id}",
    response_model=AssistSessionDetail,
    summary="One assist session, with what it produced",
)
def get_assist_session(
    session_id: int = Path(..., gt=0),
    note_limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(require_project_role(ProjectRole.VIEWER)),
):
    """The review view for a finished (or running) assist session.

    v2.284.0 — assist was the one workflow with no way to look back at what an
    agent did: plans and recon sessions each have a detail page, assist had a
    start dialog that listed live sessions and nothing else.  That is backwards
    for the workflow that runs interactively and can write notes under the
    operator's own name.

    Notes are included inline rather than behind another endpoint because they
    are the session's only durable output — everything else it did was a read,
    and the read trail is the separate api-activity feed.
    """
    session = (
        db.query(AssistSession)
        .options(joinedload(AssistSession.agent_session))
        .filter(
            AssistSession.id == session_id,
            # Scope to the path project: an assist session id from another
            # project must 404 here, not leak its purpose and note bodies.
            AssistSession.project_id == project.id,
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Assist session not found")

    username = (
        db.query(User.username).filter(User.id == session.started_by_id).scalar()
        if session.started_by_id
        else None
    )
    key_expires_at = (
        db.query(func.max(APIKey.expires_at))
        .filter(
            APIKey.agent_session_id == session.agent_session_id,
            APIKey.is_active.is_(True),
        )
        .scalar()
    )
    now = datetime.now(timezone.utc)

    notes: List[AssistSessionNote] = []
    note_total = 0
    if session.agent_session_id is not None:
        note_q = (
            db.query(Annotation, Host.ip_address, Host.hostname)
            .outerjoin(Host, Annotation.host_id == Host.id)
            .filter(Annotation.agent_session_id == session.agent_session_id)
        )
        note_total = note_q.count()
        notes = [
            AssistSessionNote(
                id=a.id,
                host_id=a.host_id,
                host_ip=ip,
                hostname=hostname,
                body=a.body,
                status=a.status.value if hasattr(a.status, "value") else a.status,
                created_at=a.created_at,
            )
            for a, ip, hostname in (
                note_q.order_by(Annotation.created_at.desc()).limit(note_limit).all()
            )
        ]

    activity = _session_activity(db, [session.id]).get(session.id)
    # v2.338.0 — feedback is stamped with the unified session id (from the
    # key), and only optionally with this detail row's id; count by either.
    feedback_filter = AgentFeedback.assist_session_id == session.id
    if session.agent_session_id is not None:
        feedback_filter = feedback_filter | (
            AgentFeedback.agent_session_id == session.agent_session_id
        )
    feedback_count = (
        db.query(func.count(AgentFeedback.id)).filter(feedback_filter).scalar()
    ) or 0

    # Detail EXTENDS the list row, so the shared fields are mapped once. The two
    # used to build the same 18 fields independently.
    probe = _probe_source(session)
    return AssistSessionDetail(
        **_session_row(
            session,
            username,
            key_expires_at,
            now=now,
            note_count=note_total,
            activity=activity,
        ).model_dump(),
        environment=probe.environment,
        environment_probed_at=probe.environment_probed_at,
        agent_model=probe.generated_by_model,
        agent_tool=probe.generated_by_tool,
        prompt_version=probe.prompt_version,
        notes=notes,
        feedback_count=feedback_count,
    )
