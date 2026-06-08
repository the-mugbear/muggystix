"""
Shared FastAPI dependencies for project-scoped endpoints.
"""

import hashlib
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List

from fastapi import Depends, Header, HTTPException, Path, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_auth import User, UserRole, APIKey
from app.db.models_agent import Agent, AgentApiCall
from app.api.v1.endpoints.auth import get_current_user
from app.core.security import check_permissions

# Sentinels: request.state.scoped_plan_id and request.state.scoped_scope_id
# are unset for JWT-authed requests that never went through the agent auth
# dep.  Agent-authed requests set them based on which column is populated
# on the api_keys row:
#   - test_plan_id set  → scoped_plan_id = int, scoped_scope_id = None
#   - scope_id set      → scoped_plan_id = None, scoped_scope_id = int   (v2.11.0)
#   - both null         → legacy/global key, both sentinels = None
# The two scope columns are mutually exclusive by convention (recon keys
# are scope-bound, plan keys are plan-bound, neither sets both).

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent authentication
# ---------------------------------------------------------------------------

_agent_bearer = HTTPBearer(auto_error=False)

# How long a persisted last_used / last_activity_at value remains "fresh"
# before the auth path will write a new one.  Trades audit-log fidelity
# (which was per-request before) for vastly fewer hot-path writes.  The
# call log already records every request with sub-second precision; this
# pair is only useful for "when did this agent last show signs of life",
# which doesn't need second-level resolution.
_AGENT_ACTIVITY_DEBOUNCE_SECONDS = 60.0

# Rate-limit window — kept in sync with the documented per-minute limit
# on Agent.rate_limit_rpm.
_AGENT_RATE_WINDOW_SECONDS = 60.0

# In-process sliding-window of recent agent calls, per agent_id.
# Supplements the DB-backed count: the audit rows the DB count reads are
# now written by a *post-response* BackgroundTask (agent_api_log_service),
# so under a burst the in-flight requests aren't persisted yet and the DB
# count lags (and reads 0 entirely if the background writer is failing) —
# i.e. the limiter would fail open exactly under load.  This per-worker
# deque counts requests synchronously at auth time, closing that race for
# bursts that land on one worker; the DB count remains the cross-worker
# ceiling.  Effective limit = max(db_count, in-process count).
_AGENT_RECENT_CALLS: "Dict[int, Deque[float]]" = {}
_AGENT_RECENT_CALLS_LOCK = threading.Lock()


def get_current_agent(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_agent_bearer),
    db: Session = Depends(get_db),
) -> Agent:
    # v2.91.4 (third code review #3) — switched from `async def` to `def`.
    # Body is fully synchronous (db.query, db.commit); see
    # auth.get_current_user for the rationale.  FastAPI dispatches `def`
    # deps to its thread pool, keeping the event loop free.
    """Authenticate an AI agent via API key.

    Accepts the key in either of two forms:
      - ``X-API-Key: nm_agent_...`` header  (preferred for agents)
      - ``Authorization: Bearer nm_agent_...`` header  (also accepted)

    Looks up the key hash in the api_keys table (agent_id IS NOT NULL).
    Returns the Agent record with its fixed project_id for data scoping.
    """
    # Prefer X-API-Key header; fall back to Authorization: Bearer
    token = request.headers.get("x-api-key")
    if not token and credentials:
        token = credentials.credentials
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing agent API key — provide X-API-Key or Authorization: Bearer header",
        )
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    api_key_obj = (
        db.query(APIKey)
        .filter(
            APIKey.key_hash == key_hash,
            APIKey.is_active.is_(True),
            APIKey.agent_id.isnot(None),
        )
        .first()
    )
    if not api_key_obj:
        raise HTTPException(status_code=401, detail="Invalid agent API key")

    if api_key_obj.expires_at is not None:
        # Some backends/drivers (and SQLite) hand back a tz-naive datetime
        # even for a DateTime(timezone=True) column.  Comparing that to a
        # tz-aware now() raises TypeError and 500s every agent request, so
        # normalise to UTC-aware before comparing.
        expires_at = api_key_obj.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Agent API key expired")

    agent = (
        db.query(Agent)
        .filter(Agent.id == api_key_obj.agent_id, Agent.is_active.is_(True))
        .first()
    )
    if not agent:
        raise HTTPException(status_code=401, detail="Agent inactive or not found")

    # Stash the key's scope bindings on request.state so downstream
    # deps (require_plan_scope, require_recon_scope, deny_scoped_keys)
    # can enforce them without re-fetching the APIKey row.
    # None = the dimension is unused; only one of the two will ever
    # be set on any given key (enforced by key-minting code, not by
    # the DB — see the comment in models_auth.APIKey).
    request.state.scoped_plan_id = api_key_obj.test_plan_id
    request.state.scoped_scope_id = api_key_obj.scope_id
    # v2.45.0 — recon keys now bind to a specific session, not just the
    # scope.  ``_load_recon_session`` reads this off request.state to
    # resolve the call's session deterministically (no "newest active"
    # heuristic, no cross-agent collision between concurrent recons).
    # NULL on legacy recon keys minted pre-v2.45.0 and on every
    # plan-scoped key — the loader falls back to the heuristic in that
    # case.  See APIKey.recon_session_id docstring for the bug history.
    request.state.scoped_recon_session_id = api_key_obj.recon_session_id
    # v2.64.0 — assist-session keys.  Mutually exclusive with the
    # other three scope columns (test_plan_id / scope_id /
    # recon_session_id).  ``require_assist_scope`` reads this off
    # request.state to validate /agent/assist/* calls; the audit
    # middleware reads it to attribute the call row to the session.
    request.state.scoped_assist_session_id = api_key_obj.assist_session_id

    # v2.116.0 (WS2c) — the workflow discriminator that replaces the
    # four-way deny-matrix.  Derived from the bound AgentSession (the new
    # single scope binding); falls back to the legacy columns for any key
    # not yet backfilled.  Normalized to "plan" (covers plan_generation +
    # execution, which the deny-matrix treats identically), "recon",
    # "assist", or None (unscoped global key).
    agent_session = api_key_obj.agent_session
    if agent_session is not None:
        _wf = agent_session.workflow
        if _wf in ("plan_generation", "execution"):
            request.state.key_workflow = "plan"
            request.state.key_plan_id = agent_session.plan_id
        elif _wf == "recon":
            request.state.key_workflow = "recon"
            request.state.key_plan_id = None
        elif _wf == "assist":
            request.state.key_workflow = "assist"
            request.state.key_plan_id = None
        else:
            request.state.key_workflow = None
            request.state.key_plan_id = None
    elif api_key_obj.test_plan_id is not None:
        request.state.key_workflow = "plan"
        request.state.key_plan_id = api_key_obj.test_plan_id
    elif api_key_obj.scope_id is not None:
        request.state.key_workflow = "recon"
        request.state.key_plan_id = None
    elif api_key_obj.assist_session_id is not None:
        request.state.key_workflow = "assist"
        request.state.key_plan_id = None
    else:
        request.state.key_workflow = None
        request.state.key_plan_id = None
    request.state.agent_session_id = (
        agent_session.id if agent_session is not None else None
    )

    # v2.24.0 — agent_api_call middleware reads these after the response
    # is returned (when request.state survives via Starlette's request
    # lifecycle) to write the call-log row.  Capturing the prefix only,
    # never the raw key.
    request.state.agent_id = agent.id
    request.state.agent_project_id = agent.project_id
    request.state.api_key_id = api_key_obj.id
    request.state.api_key_prefix = api_key_obj.key_prefix

    # v2.26.0 — debounce last_used / last_activity_at writes.
    # Previously every authenticated agent request triggered an
    # UPDATE on both ``api_keys`` and ``agents``.  The two columns
    # are used for "when did this key/agent last show signs of life"
    # — coarse signals that don't need second-level resolution
    # (the per-request audit trail lives in agent_api_calls).  Skip
    # the write when the persisted value is younger than the
    # debounce window.  The persisted value is itself the source of
    # truth, so this works across workers without any shared state.
    now = datetime.now(timezone.utc)
    need_commit = False

    def _stale(t):
        if t is None:
            return True
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds() >= _AGENT_ACTIVITY_DEBOUNCE_SECONDS

    if _stale(api_key_obj.last_used):
        api_key_obj.last_used = now
        need_commit = True
    if _stale(agent.last_activity_at):
        agent.last_activity_at = now
        need_commit = True
    if need_commit:
        db.commit()

    return agent


def check_agent_rate_limit(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> Agent:
    # v2.91.4 (third code review #3) — synchronous body (one COUNT query).
    # Plain `def` so FastAPI runs it in the thread pool.
    """Enforce per-agent sliding-window rate limiting.

    v2.26.0 — counts rows in ``agent_api_calls`` within the last
    ``_AGENT_RATE_WINDOW_SECONDS`` instead of maintaining an
    in-process dict.  Two side benefits:

    1. The limit is enforced **globally** across Uvicorn workers
       (previous behaviour: N workers each enforced the limit
       independently, so the effective limit was N × rate_limit_rpm).
    2. No new infrastructure — the audit log added in v2.24.0
       already records every authenticated agent request with an
       indexed ``(agent_id, created_at)`` lookup path.

    The audit rows the DB count reads are written by a *post-response*
    BackgroundTask (v2.91.4), so the DB count lags the actual request
    rate under burst and reads 0 if the background writer is failing —
    the limiter would fail open exactly when it matters.  We therefore
    take the **max** of the lagging DB count (cross-worker ceiling) and
    an in-process sliding-window count recorded synchronously here
    (closes the deferred-write race for a burst on one worker).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_AGENT_RATE_WINDOW_SECONDS)
    db_count = (
        db.query(func.count(AgentApiCall.id))
        .filter(
            AgentApiCall.agent_id == agent.id,
            AgentApiCall.created_at >= cutoff,
        )
        .scalar()
        or 0
    )

    # In-process count of prior requests still inside the window (pruned),
    # recorded synchronously and not dependent on the deferred audit write.
    now_m = time.monotonic()
    window_start = now_m - _AGENT_RATE_WINDOW_SECONDS
    with _AGENT_RECENT_CALLS_LOCK:
        dq = _AGENT_RECENT_CALLS.setdefault(agent.id, deque())
        while dq and dq[0] < window_start:
            dq.popleft()
        inproc_prior = len(dq)
        effective_prior = max(db_count, inproc_prior)
        if effective_prior >= agent.rate_limit_rpm:
            # Don't record the rejected call — a client hammering past the
            # limit must not extend its own lockout indefinitely.
            if not dq:
                # Window fully expired but the DB count tripped the limit;
                # don't leave an empty deque lingering in the dict (it would
                # otherwise accumulate one entry per agent ever seen).
                _AGENT_RECENT_CALLS.pop(agent.id, None)
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        dq.append(now_m)
    return agent


def require_plan_scope(
    request: Request,
    plan_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
) -> Agent:
    # v2.91.4 (third code review #3) — body is sync.  `def` so the
    # FastAPI dispatcher uses the thread pool.
    """Rate-limited agent auth + per-plan scope enforcement.

    Use on any agent endpoint that takes a ``plan_id`` path parameter.
    If the caller's API key is scoped to a specific test plan (the
    normal case for keys minted by ``/generate`` or ``/execute``), the
    request ``plan_id`` must match — otherwise 403.  Unscoped keys
    (legacy global agent keys) pass through unchanged.

    **Also rejects scope-bound (recon) keys outright** — recon keys
    have no business touching test plans.  Use ``require_recon_scope``
    on recon endpoints instead.  Same for assist keys — they're
    read-only and have no business creating or touching plans.
    """
    # WS2c — single workflow check (was a three-way deny-matrix over the
    # legacy scoped_plan/scope/assist columns).
    workflow = getattr(request.state, "key_workflow", None)
    if workflow == "recon":
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a reconnaissance run against "
                "a scope and cannot access test plan endpoints. Recon "
                "keys upload scanner output; they do not create plans. "
                "Use /agent/recon/* or generate a plan-generation key."
            ),
        )
    if workflow == "assist":
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a read-only assist session "
                "and cannot access test plan endpoints. Use /agent/assist/* "
                "for queries; generate a plan-generation key from the "
                "Test Plans UI for plan work."
            ),
        )
    if workflow == "plan":
        key_plan_id = getattr(request.state, "key_plan_id", None)
        if key_plan_id is not None and key_plan_id != plan_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This API key is scoped to a different test plan. "
                    "Per-plan keys cannot access endpoints for other plans — "
                    "generate a new key for the plan you're working on."
                ),
            )
    return agent


def require_recon_scope(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
) -> Agent:
    """Rate-limited agent auth + recon-scope enforcement.

    Use on any ``/agent/recon/*`` endpoint.  The caller's API key must
    have ``scope_id`` set — plan-scoped keys (from /generate or
    /execute), assist keys, and unscoped global keys are all rejected.
    The ReconSession / scope the key binds to is available on
    ``request.state.scoped_scope_id`` for the handler to use.

    v2.11.0 — part of the agentic recon ingest workflow.  Recon is
    strictly an ingestion pipeline; the agent discovers hosts, not
    plans them.
    """
    workflow = getattr(request.state, "key_workflow", None)
    if workflow == "assist":
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a read-only assist session "
                "and cannot access recon endpoints. Recon ingest writes "
                "scan output; assist keys are read-only. Start a recon "
                "session via /projects/{id}/scopes/{scope_id}/recon/start."
            ),
        )
    if workflow != "recon":
        raise HTTPException(
            status_code=403,
            detail=(
                "This endpoint requires a reconnaissance-scoped API key "
                "(minted by POST /projects/{id}/scopes/{scope_id}/recon/start). "
                "Plan-generation and execution keys do not have access to "
                "recon endpoints."
            ),
        )
    return agent


def require_assist_scope(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
) -> Agent:
    """Rate-limited agent auth + assist-session scope enforcement.

    v2.64.0 — used on every ``/agent/assist/*`` endpoint.  The
    caller's API key must have ``assist_session_id`` set; plan-
    scoped, recon-scoped, and unscoped global keys are all rejected.
    The AssistSession id is available on
    ``request.state.scoped_assist_session_id``; the handler
    resolves the session row from there.

    Assist sessions are intentionally read-only (no execution, no
    plan creation, no follow mutations in v1).  The router-level
    decision keeps things simple: only GETs are exposed under
    /agent/assist/, plus a single POST for the environment probe.
    """
    workflow = getattr(request.state, "key_workflow", None)
    if workflow == "plan":
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is plan-scoped and cannot access assist "
                "endpoints. Start an assist session via POST "
                "/projects/{id}/assist/start to mint an assist key."
            ),
        )
    if workflow == "recon":
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is recon-scoped and cannot access assist "
                "endpoints. Start an assist session via POST "
                "/projects/{id}/assist/start to mint an assist key."
            ),
        )
    if workflow != "assist":
        raise HTTPException(
            status_code=403,
            detail=(
                "This endpoint requires an assist-scoped API key "
                "(minted by POST /projects/{id}/assist/start). "
                "Plan-generation, execution, and recon keys do not "
                "have access to assist endpoints."
            ),
        )
    return agent


async def deny_scoped_keys(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
) -> Agent:
    """Block scope-bound keys (plan OR recon) from global endpoints.

    Use on mutating endpoints that are not tied to a specific plan or
    scope but should not be callable by a scope-bound key (e.g.
    creating a new test plan — a per-plan key already belongs to a
    different plan and has no business spawning another; a recon key
    has no business creating plans at all).  Unscoped keys pass
    through.
    """
    workflow = getattr(request.state, "key_workflow", None)
    if workflow is not None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a single test plan, recon "
                "session, or assist session and cannot act on other "
                "workflows. Use an unscoped agent key or call "
                "/projects/{id}/test-plans/ with JWT auth instead."
            ),
        )
    return agent


# ---------------------------------------------------------------------------
# Project access
# ---------------------------------------------------------------------------

async def get_current_project(
    project_id: int = Path(..., description="Project ID", gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    """Validate that the project exists and the current user has access.

    Global admins can access any project. Other users must have a
    ProjectMembership row for the given project.

    Returns the Project instance for use in endpoint handlers.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.is_archived:
        raise HTTPException(status_code=410, detail="Project is archived")

    # Global admins bypass membership check
    if current_user.role == UserRole.ADMIN:
        return project

    membership = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == current_user.id,
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this project")

    return project


def get_project_membership(
    project_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectMembership | None:
    """Return the user's membership for the given project, or None for global admins."""
    if current_user.role == UserRole.ADMIN:
        return None  # admins bypass
    return db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == current_user.id,
    ).first()


def is_project_admin(db: Session, project_id: int, user: User) -> bool:
    """True when ``user`` has admin authority over ``project_id``.

    Either a global admin, or a project member whose ProjectMembership
    role satisfies the admin tier.  Plain function (not a dependency)
    so route handlers can call it inline for per-row ownership checks
    — e.g. "the session owner OR a project admin may abandon this
    session" (v2.45.9).
    """
    if user.role == UserRole.ADMIN:
        return True
    membership = db.query(ProjectMembership).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == user.id,
    ).first()
    return membership is not None and check_permissions(membership.role, ProjectRole.ADMIN)


def require_project_role(required_role: str):
    """Dependency factory that checks per-project role.

    Global admins bypass. Otherwise the user's ProjectMembership.role
    is checked against the role hierarchy.
    """
    def checker(
        project_id: int = Path(..., gt=0),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> User:
        # Global admins always pass
        if current_user.role == UserRole.ADMIN:
            return current_user

        membership = db.query(ProjectMembership).filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == current_user.id,
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this project")

        if not check_permissions(membership.role, required_role):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient project role. Required: {required_role}",
            )
        return current_user

    return checker
