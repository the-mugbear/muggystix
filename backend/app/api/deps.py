"""
Shared FastAPI dependencies for project-scoped endpoints.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import Depends, Header, HTTPException, Path, Request, UploadFile, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import CompileError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_auth import User, UserRole, UserSession, APIKey
from app.db.models_agent import Agent, AgentRateBucket, AgentSession, AgentSessionWorkflow
from app.core.config import settings
from app.core.security import check_permissions, verify_token
# Re-exported: agent_browse reads it from here.  Defined in the service layer
# (v2.338.0) so the session sweep can share it without importing this module.
from app.services.agent_key_ttl import session_renewal_deadline  # noqa: F401

# What the agent auth dependency leaves on ``request.state`` for handlers and
# the audit middleware (v2.337.0 — a key binds to one project session, never to
# a plan or scope):
#   agent_id, agent_project_id, api_key_id, api_key_prefix, key_expires_at,
#   agent_session_id, agent_session_workflow, key_operator_id (+ key_operator_role
#   and key_operator_is_admin once ``enforce_agent_operator_access`` has run).
# ``key_operator_id`` is THE operator (who the key acts for, who its writes are
# attributed to) and ``agent_project_id`` THE project; a handler reads these
# two rather than ``agent.owner_id`` / ``session.started_by_id`` or
# ``agent.project_id`` / ``session.project_id``.
# None of these are set for JWT-authed requests.

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User (JWT) authentication
# ---------------------------------------------------------------------------
#
# These dependencies lived in ``endpoints/auth.py`` until the 2026-10-01
# review (B4): this module and ~45 routers imported them from a router file.
# ``endpoints/auth.py`` re-exports the same objects, so an existing import —
# and a test's ``dependency_overrides[get_current_user]`` — resolves to the
# one function defined here.  This module must never import from
# ``app.api.v1.endpoints`` (pinned by ``test_service_router_boundary.py``).

# Short-lived purpose claim minted after password (but before TOTP) succeeds.
# A token carrying it is NOT a session: it has no UserSession row, and
# get_current_user rejects the purpose explicitly (belt-and-suspenders).
TWO_FACTOR_CHALLENGE_PURPOSE = "2fa_challenge"

security = HTTPBearer()
# Same scheme without the automatic 401 — used where the token is only read
# for context after get_current_user has authenticated the request.
optional_bearer = HTTPBearer(auto_error=False)

# v2.91.3 (code review #6) — debounce window for UserSession.last_activity
# updates on the get_current_user dep.  Mirrors the agent-side debounce
# constant below; see the get_current_user docstring for why
# coarse-grained resolution is fine here.
_USER_SESSION_ACTIVITY_DEBOUNCE_SECONDS = 60.0


def get_client_info(request: Request) -> Dict[str, Optional[str]]:
    """Extract client information from request"""
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent")
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    # v2.91.4 (third code review #3) — switched from `async def` to
    # plain `def`.  Pre-fix this dep was `async def` but every call
    # inside it (verify_token / db.query.first() / db.commit())
    # is synchronous psycopg2 / bcrypt work.  FastAPI runs `async
    # def` deps directly on the event loop, so on every
    # authenticated request the loop blocked on two SELECTs + an
    # UPDATE; a slow DB stalled unrelated requests on the same
    # Uvicorn worker.  Switching to `def` lets FastAPI dispatch
    # this dep to its thread pool, freeing the loop.  Same
    # contract — the caller awaits the same Depends().
    """
    Get current authenticated user from JWT token
    """
    token = credentials.credentials
    payload = verify_token(token)

    # A 2FA-challenge token proves password-but-not-yet-TOTP; it must never
    # authenticate a request.  (It also has no session row, so the lookup
    # below would reject it anyway — this is the explicit, earlier guard.)
    if payload.get("purpose") == TWO_FACTOR_CHALLENGE_PURPOSE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-factor authentication not completed",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    # `sub` is a numeric user id at mint time, but a malformed or
    # foreign-issued token could carry a non-numeric subject; int() would
    # then raise ValueError and escape as a 500.  Auth-boundary type
    # failures must be 401, not 500.
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    user = db.query(User).filter(User.id == user_id_int).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )

    # Check if session is still valid
    token_jti = payload.get("jti")
    session = db.query(UserSession).filter(
        UserSession.token_jti == token_jti,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > datetime.now(timezone.utc)
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or revoked"
        )

    # v2.91.3 (code review #6) — debounce the per-request session
    # activity write.  Pre-fix every authenticated user request issued
    # an UPDATE + commit on user_sessions, which on a polling-heavy UI
    # turns read traffic into write traffic with all the WAL + row-
    # contention costs.  The agent path was debounced in v2.26.0
    # (see _AGENT_ACTIVITY_DEBOUNCE_SECONDS below); apply the same
    # pattern here.  ``last_activity`` is used as a "when did this
    # user last show signs of life" coarse signal — second-level
    # resolution isn't required (the per-request audit trail lives
    # elsewhere).  Stateless across workers because the persisted
    # value is itself the source of truth.
    now = datetime.now(timezone.utc)
    prior = session.last_activity
    if prior is not None and prior.tzinfo is None:
        prior = prior.replace(tzinfo=timezone.utc)
    if prior is None or (now - prior).total_seconds() >= _USER_SESSION_ACTIVITY_DEBOUNCE_SECONDS:
        session.last_activity = now
        db.commit()

    return user


def require_role(required_role: str):
    """Decorator to require specific user role"""
    def role_checker(current_user: User = Depends(get_current_user)):
        if not check_permissions(current_user.role, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {required_role}"
            )
        return current_user
    return role_checker


def require_password_changed(
    current_user: User = Depends(get_current_user),
) -> User:
    """Post-login account-readiness gate, applied to every data endpoint.

    Blocks API access (403 with a machine-readable detail the frontend
    intercepts) until the user has finished account setup:
      * ``password_change_required`` — a forced password change is pending.
      * ``two_factor_setup_required`` — mandatory 2FA (``REQUIRE_2FA``) is on
        and the user hasn't enrolled yet.

    Password change is checked first (most urgent).  The ``/auth/*`` surface —
    login, logout, change-password, profile, and the ``/auth/2fa/*`` enrollment
    endpoints — is intentionally NOT behind this gate, so a blocked user can
    still complete setup.
    """
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_change_required",
        )
    if settings.REQUIRE_2FA and not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="two_factor_setup_required",
        )
    return current_user


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

# v2.300.0 — the per-worker deque (`_AGENT_RECENT_CALLS`) and its lock and
# sweep threshold are gone.  They existed only to paper over a DB count that
# lagged because it read a post-response audit log; enforcement no longer reads
# that log at all.  Shared state now lives in `agent_rate_buckets`, which is
# where a limit spanning four Uvicorn workers has to live.
#
# Sweep old buckets every Nth admitted request for an agent.  Sampling, not a
# per-request delete: the statement is indexed and small, but on the hot path
# "small and pointless" still costs a round trip.
_AGENT_RATE_SWEEP_EVERY = 500


#: Path an agent posts to in order to renew its own key. Named once so the
#: 401 payload and the route can never drift.
AGENT_SESSION_RENEW_PATH = "/api/v1/agent/session/renew"


def key_is_renewable(agent_session) -> bool:
    """Can a key bound to this session still be renewed?

    v2.304.0.  Deliberately independent of whether the key has already expired:
    an agent that blocked for six hours on a scan discovers the lapse only when
    it tries to upload, and refusing it there discards work that has already
    been done. Renewal stays open while the SESSION is alive and under its
    maximum lifetime; past that, expiry is terminal and the operator starts a
    new session.
    """
    if agent_session is None:
        return False
    status = getattr(agent_session, "status", None)
    status = status.value if hasattr(status, "value") else status
    if status != "active":
        return False
    deadline = session_renewal_deadline(agent_session)
    if deadline is None:
        return False
    return datetime.now(timezone.utc) < deadline


def _aware(t: Optional[datetime]) -> Optional[datetime]:
    """UTC-aware ``t``: some drivers hand back a naive value for a
    ``DateTime(timezone=True)`` column, and comparing it to an aware one raises."""
    if t is not None and t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t


@dataclass(frozen=True)
class _AgentKeyContext:
    """Everything the agent auth chain decides from, read in one statement.

    ``operator_*`` describe the one person the key acts for: the session's
    starter, else the agent's owner.  ``agent`` is None when the key's agent
    is missing or inactive.
    """
    api_key: APIKey
    session: Optional[AgentSession]
    agent: Optional[Agent]
    project_archived: bool
    operator_id: Optional[int]
    operator_active: bool
    operator_is_admin: bool
    operator_password_changed_at: Optional[datetime]
    membership_role: Optional[str]


def _load_agent_key(db: Session, token: str) -> Optional[_AgentKeyContext]:
    """The active agent key for ``token`` with its session, agent, project
    state, operator and the operator's membership — one joined select, so an
    agent call's authentication and authorization cost one read."""
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    row = (
        db.query(
            APIKey, AgentSession, Agent, Project.is_archived,
            User.id, User.is_active, User.role, User.password_changed_at,
            ProjectMembership.role,
        )
        .select_from(APIKey)
        .outerjoin(AgentSession, AgentSession.id == APIKey.agent_session_id)
        .outerjoin(Agent, and_(Agent.id == APIKey.agent_id, Agent.is_active.is_(True)))
        .outerjoin(Project, Project.id == Agent.project_id)
        .outerjoin(User, User.id == func.coalesce(AgentSession.started_by_id, Agent.owner_id))
        .outerjoin(
            ProjectMembership,
            and_(
                ProjectMembership.project_id == Agent.project_id,
                ProjectMembership.user_id == User.id,
            ),
        )
        .filter(
            APIKey.key_hash == key_hash,
            APIKey.is_active.is_(True),
            APIKey.agent_id.isnot(None),
        )
        .first()
    )
    if row is None:
        return None
    (api_key, session, agent, archived,
     operator_id, operator_active, operator_role, password_changed_at, membership_role) = row
    return _AgentKeyContext(
        api_key=api_key,
        session=session,
        agent=agent,
        project_archived=bool(archived),
        operator_id=operator_id,
        operator_active=bool(operator_active),
        operator_is_admin=operator_role == UserRole.ADMIN,
        operator_password_changed_at=password_changed_at,
        membership_role=membership_role,
    )


def _issued_before_password_change(ctx: _AgentKeyContext) -> bool:
    """True when the key was issued before its operator's password last changed.

    A password change or reset is how a compromised account is taken back, so
    nothing issued under the old password may keep working.  Decided by the
    KEY's issue time: a resumed session carries a key minted by the operator
    after the change, and that key is theirs.
    """
    issued = _aware(ctx.api_key.created_at) or _aware(
        ctx.session.started_at if ctx.session is not None else None
    )
    changed = _aware(ctx.operator_password_changed_at)
    return issued is not None and changed is not None and issued < changed


_CREDENTIALS_CHANGED_DETAIL = {
    "error": "operator_credentials_changed",
    "recoverable": False,
    "message": (
        "This key was issued before its operator's password was changed, so it "
        "no longer works. Ask the operator to start a new session; save any "
        "output you are holding first."
    ),
}


def _commit_keeping_loaded(db: Session) -> None:
    """Commit without expiring what the auth chain has loaded.

    The chain commits its own bookkeeping (last-used stamps, the rate bucket)
    before the handler runs; an expiring commit would make every later read of
    ``agent`` re-select a row that was read a moment ago in this request.
    """
    previous = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.commit()
    finally:
        db.expire_on_commit = previous


def _expired_key_detail(session) -> Dict[str, object]:
    """The body of a 401 raised for an expired key bound to ``session``.

    Structured because the caller is usually mid-workflow holding output it
    cannot reproduce cheaply, and "expired" vs "revoked" are the same status
    code but opposite situations. ``recoverable`` is the field an agent
    branches on.
    """
    if key_is_renewable(session):
        return {
            "error": "key_expired",
            "recoverable": True,
            "renew_path": AGENT_SESSION_RENEW_PATH,
            "message": (
                "Your API key expired, but its session is still active. POST to "
                f"{AGENT_SESSION_RENEW_PATH} with this same key to extend it, "
                "then RETRY the request you were making. Do not re-run any scan "
                "or command whose output you are already holding."
            ),
        }
    return {
        "error": "key_expired",
        "recoverable": False,
        "message": (
            "Your API key expired and its session is no longer renewable "
            "(ended, or past its maximum lifetime). Ask the operator to start a "
            "new session. Save any output you are holding to a file first — a "
            "new key will not bring this one back."
        ),
    }


def authenticate_for_renewal(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_agent_bearer),
) -> APIKey:
    """Authenticate a key for renewal ONLY, tolerating expiry.

    v2.304.0.  This is the one place an expired key is accepted, and it exists
    because the alternative is discarding work: an agent blocks for hours on a
    scan, its key lapses while it waits, and it finds out at upload time. Every
    other check still applies — the key must be active, its agent must be
    active, and its session must be alive and under its maximum lifetime.

    Deliberately NOT a general auth dependency. It grants exactly one action:
    extending the deadline on the key that was presented. It cannot read or
    write project data.
    """
    token = x_api_key or (credentials.credentials if credentials else None)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing agent API key — provide X-API-Key or Authorization: Bearer header",
        )
    ctx = _load_agent_key(db, token)
    # A revoked key is gone for good — is_active is the operator's kill switch
    # and renewal must never route around it.
    if ctx is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked agent API key")
    api_key_obj, agent = ctx.api_key, ctx.agent
    if not agent:
        raise HTTPException(status_code=401, detail="Agent inactive or not found")

    # Renewal extends a credential, so a key the operator's password change
    # has cancelled must not be extended either.
    if _issued_before_password_change(ctx):
        raise HTTPException(status_code=401, detail=_CREDENTIALS_CHANGED_DETAIL)

    if not key_is_renewable(ctx.session):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "session_not_renewable",
                "recoverable": False,
                "message": (
                    "This key's session has ended or passed its maximum "
                    "lifetime, so it cannot be renewed. Save any output you are "
                    "holding to a file and ask the operator to start a new "
                    "session."
                ),
            },
        )
    # v2.307.0 — stamp the FULL attribution set, not just agent + prefix.
    #
    # The audit middleware discards any non-5xx request that lacks both an
    # agent id and a project id (agent_api_log_service — the table's CHECK
    # requires attribution or an error class). Renewal is mounted outside the
    # normal dependency chain, so nothing else fills these in: stamping only
    # agent_id meant renewals wrote **no audit row at all**, while the plan
    # claimed every renewal was audited. A credential-extending call is exactly
    # the kind that has to be answerable after the fact.
    request.state.agent_id = agent.id
    request.state.agent_project_id = agent.project_id
    request.state.api_key_id = api_key_obj.id
    request.state.api_key_prefix = api_key_obj.key_prefix
    session = ctx.session
    request.state.agent_session_id = session.id if session is not None else None
    # Same attribution the normal chain stamps (see get_current_agent), so a
    # renewal lands on the session's timeline rather than as an orphan row.
    request.state.agent_session_workflow = (
        session.workflow if session is not None else None
    )
    return api_key_obj


# v2.309.0 (consolidation Phase 5) — the capability system is gone.
#
# ``resolve_capabilities``, ``require_capability`` and
# ``enforce_capability_row_scope`` lived here, alongside an
# ``AgentCapability`` vocabulary and a row-level ``ASSIGNED`` constraint. They
# were a second authorization model sitting beside the product's own RBAC, and
# only assist ever used them — the other three workflows were grandfathered
# past the check entirely (``if workflow != "assist": return
# LEGACY_WRITE_CAPABILITIES``), which is the tell that the model was inherited
# rather than chosen.
#
# What replaces them: ``enforce_agent_operator_access``. A key does what its
# operator may do, checked per request against the same roles a person is
# checked against. One model instead of two.
#
# The user-facing consequence, decided deliberately: **an operator can no
# longer start a deliberately read-only assist session.** Read-only was the
# default, so most sessions carried it by inertia rather than choice, and
# nothing was porting or draining — every assist session in the deployment was
# already ended. Analysts are the overwhelming majority of users, and their
# agent now carries their own authority. Auditors and viewers get read-only
# anyway, because that is what *they* can do.


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
    # One read for the whole chain: this dependency, the rate limiter and
    # ``enforce_agent_operator_access`` all decide from it.
    ctx = _load_agent_key(db, token)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Invalid agent API key")
    api_key_obj = ctx.api_key

    if api_key_obj.expires_at is not None:
        # Some backends/drivers (and SQLite) hand back a tz-naive datetime
        # even for a DateTime(timezone=True) column.  Comparing that to a
        # tz-aware now() raises TypeError and 500s every agent request, so
        # normalise to UTC-aware before comparing.
        expires_at = api_key_obj.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            # v2.304.0 — a structured 401, because "expired" and "revoked" are
            # the same status code but completely different situations for the
            # caller, and the caller is usually holding hours of scan output at
            # this point.  Flat prose gave it no way to tell whether retrying
            # was worth anything.  ``recoverable`` says: renew with THIS key and
            # try again.
            raise HTTPException(
                status_code=401,
                detail=_expired_key_detail(ctx.session),
            )

    agent = ctx.agent
    if not agent:
        raise HTTPException(status_code=401, detail="Agent inactive or not found")

    # The key's binding is its AgentSession.  Every agent key carries one —
    # mint paths set it and a backfill guaranteed it — so a null binding on an
    # agent key is an orphaned/corrupt credential.  Fail CLOSED rather than
    # treat it as unscoped, which was historically the MOST-privileged outcome
    # (unscoped global keys, abolished v2.295.0).
    agent_session = ctx.session
    if agent_session is None:
        logger.warning(
            "rejecting agent key %s (agent_id=%s) with no AgentSession binding — "
            "start an agent session from the project UI to mint one",
            api_key_obj.key_prefix, api_key_obj.agent_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "This agent key is not bound to a session. Start an agent "
                "session from the project's Agent Sessions page to mint one."
            ),
        )
    if agent_session.status != "active":
        # An ended session's keys are revoked on the way out, so this is only
        # reachable for a session lapsed by the sweep between two requests.
        # A key must not outlive its session.
        raise HTTPException(
            status_code=401,
            detail={
                "error": "session_ended",
                "recoverable": False,
                "message": (
                    "This key's session has ended. Ask the operator to start a "
                    "new session; save any output you are holding first."
                ),
            },
        )
    if agent_session.workflow not in {w.value for w in AgentSessionWorkflow}:
        # Fail CLOSED on a row this code can't classify (data corruption).
        logger.warning(
            "agent key %s bound to agent_session %s with unrecognized workflow "
            "%r — denying",
            api_key_obj.key_prefix, agent_session.id, agent_session.workflow,
        )
        raise HTTPException(
            status_code=403,
            detail="API key is bound to an unrecognized session kind; start a new session.",
        )
    if agent_session.project_id != agent.project_id:
        # The session and the agent name the same project by construction.
        # Handlers scope data by one or the other, and the operator's role is
        # checked against one project, so a key whose two disagree is refused.
        logger.warning(
            "agent key %s: session %s is on project %s but its agent %s is on %s — denying",
            api_key_obj.key_prefix, agent_session.id, agent_session.project_id,
            agent.id, agent.project_id,
        )
        raise HTTPException(
            status_code=403,
            detail="API key's session and agent are on different projects; start a new session.",
        )
    if _issued_before_password_change(ctx):
        raise HTTPException(status_code=401, detail=_CREDENTIALS_CHANGED_DETAIL)

    # v2.337.0 — a key no longer binds a workflow, a plan or a scope; what it
    # writes carries the session's id.  The label is stashed only so the audit
    # middleware can attach legacy detail rows.
    request.state.agent_session_id = agent_session.id
    request.state.agent_session_workflow = agent_session.workflow

    # The ONE operator: the human this key acts for — the session's starter,
    # else the agent's owner (the same person; ``Agent.owner_id`` is NOT NULL
    # and a deleted user's agents and keys are deleted with them, so there is
    # always one).  ``enforce_agent_operator_access`` checks their role on
    # every request and agent-authored writes are attributed to them.
    request.state.key_operator_id = agent_session.started_by_id or agent.owner_id
    # What the operator gate decides from — plain values, so the request holds
    # no database rows after it has been answered.
    request.state._agent_operator_access = (
        ctx.project_archived, ctx.operator_id, ctx.operator_active,
        ctx.operator_is_admin, ctx.membership_role,
    )

    # v2.24.0 — agent_api_call middleware reads these after the response
    # is returned (when request.state survives via Starlette's request
    # lifecycle) to write the call-log row.  Capturing the prefix only,
    # never the raw key.
    request.state.agent_id = agent.id
    request.state.agent_project_id = agent.project_id
    request.state.api_key_id = api_key_obj.id
    request.state.api_key_prefix = api_key_obj.key_prefix
    # Surfaced by /agent/identity so a long-running agent can see its own TTL
    # instead of discovering it as a mid-run 401.  Read-only signal — the
    # expiry check itself already happened above.
    request.state.key_expires_at = api_key_obj.expires_at

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
    # v2.434.0 — the harness, when no MCP handshake named it: a curl agent's
    # User-Agent ("curl/8.5.0").  Written once; the handshake overwrites it.
    # The MCP loopback forwards the client's own User-Agent, and sends httpx's
    # default only when the client sent none, which names nothing.
    if not agent_session.generated_by_tool:
        ua = (request.headers.get("user-agent") or "").strip()
        if ua and not ua.startswith("python-httpx/"):
            from app.services.agent_session_service import note_agent_harness
            note_agent_harness(agent_session, ua, overwrite=False)
            need_commit = True
    if need_commit:
        _commit_keeping_loaded(db)

    return agent


def identify_agent_if_present(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_agent_bearer),
    db: Session = Depends(get_db),
) -> Optional[Agent]:
    """Attribute an agent's call on an endpoint that does not *require* an agent.

    v2.312.0.  Two MCP tools dispatch to public endpoints — ``read_agent_guide``
    to ``/agents-guide`` and ``list_tools`` to ``/references/tools`` —
    and the agent sends its key on both.  Neither endpoint looked at it, so
    ``request.state`` carried no attribution and the audit middleware dropped
    the row: a four-call assist session showed two entries, which reads as a
    quieter agent rather than as a partial record.

    Attribution comes from **authenticating the key**, never from a header the
    caller supplies, so this cannot be used to write an audit row against
    someone else's agent.

    Use only on endpoints that are already public. A missing or unusable key is
    not this endpoint's problem — it serves everyone — it simply earns no audit
    row, which is the same record an anonymous caller has always produced.
    """
    if not request.headers.get("x-api-key") and credentials is None:
        return None
    try:
        return get_current_agent(request=request, credentials=credentials, db=db)
    except HTTPException:
        return None


def check_agent_rate_limit(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> Agent:
    # v2.91.4 (third code review #3) — synchronous body (one COUNT query).
    # Plain `def` so FastAPI runs it in the thread pool.
    """Enforce the per-agent request rate, atomically across workers.

    v2.300.0 — capacity is now **reserved** at admission with a single
    ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING count`` against
    ``agent_rate_buckets``.  Postgres serializes concurrent upserts of the same
    row, so four Uvicorn workers admitting simultaneously each get a distinct,
    increasing count and one limit holds across all of them.

    What this replaces, and why neither half could work:

    * A ``COUNT`` over ``agent_api_calls``.  Those rows are written by a
      **post-response** BackgroundTask (v2.91.4), so the count excluded every
      request currently in flight — and read 0 outright if the background
      writer was failing, i.e. the limiter failed open exactly when it
      mattered.  Enforcement was reading an audit log that had not been
      written yet.
    * An in-process deque, which lives in one worker.  Taking ``max()`` of the
      two narrowed the race without closing it: a burst distributed across
      workers still passed, and *adding workers made the limit weaker* — the
      opposite of what scaling out should do.

    The window is fixed rather than sliding (see ``AgentRateBucket``): the
    trade is up to 2x ``rate_limit_rpm`` across a boundary, a bounded abuse
    ceiling in place of the unbounded one it replaces.

    A rejected request still increments the bucket — deliberately, and unlike
    the old behaviour.  With a fixed window the count cannot extend a lockout
    past the window's own expiry, so a client hammering the limit waits at most
    one window, while an attacker no longer gets retries the limiter declines
    to count.
    """
    now = datetime.now(timezone.utc)
    window_seconds = int(_AGENT_RATE_WINDOW_SECONDS)
    # Truncate to the window so every worker derives the same bucket key
    # without coordinating.
    epoch = int(now.timestamp()) // window_seconds * window_seconds
    window_start = datetime.fromtimestamp(epoch, tz=timezone.utc)

    stmt = (
        pg_insert(AgentRateBucket)
        .values(agent_id=agent.id, window_start=window_start, count=1)
        .on_conflict_do_update(
            index_elements=["agent_id", "window_start"],
            set_={"count": AgentRateBucket.__table__.c.count + 1},
        )
        .returning(AgentRateBucket.__table__.c.count)
    )
    try:
        count = db.execute(stmt).scalar_one()
        # Commit immediately: the row lock is held until this transaction ends,
        # and holding it for the request's duration would serialize every call
        # from the same agent.  get_current_agent already commits in this same
        # dependency chain, so there is no caller work to disturb.
        _commit_keeping_loaded(db)
    except (ProgrammingError, OperationalError, CompileError):
        # No ON CONFLICT support (sqlite dev), or the table is missing because
        # migrations have not run yet.  Fail OPEN rather than locking every
        # agent out of a deployment mid-upgrade — the limiter this replaces
        # also failed open, and a boot-order problem must not present as an
        # attack.  Logged so it can never be silent.
        db.rollback()
        logger.warning(
            "agent rate limiting unavailable (agent_rate_buckets not usable) - "
            "admitting the request; run migrations",
            exc_info=True,
        )
        return agent

    if count > agent.rate_limit_rpm:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Opportunistic housekeeping: drop buckets from windows nothing can be
    # counted against any more.  Sampled rather than run per request — the
    # delete is indexed and tiny, but it is still pure hot-path overhead.
    if count % _AGENT_RATE_SWEEP_EVERY == 0:
        try:
            db.query(AgentRateBucket).filter(
                AgentRateBucket.window_start
                < window_start - timedelta(seconds=window_seconds),
            ).delete(synchronize_session=False)
            db.commit()
        except Exception:  # pragma: no cover - housekeeping must never 500
            db.rollback()
            logger.warning("agent rate bucket sweep failed", exc_info=True)
    return agent


# ---------------------------------------------------------------------------
# Operator-derived authorization (v2.305.0 — consolidation Phase 1)
#
# Mutating agent routes that are NOT project-data writes. They record something
# about the session itself — its environment, its key deadline, feedback about
# the prompt — so they stay available to any key whose operator is still a
# member, regardless of role. A read-only operator needs to renew a key and
# report its environment exactly as much as anyone else.
#
# Everything else that mutates requires the operator to hold ANALYST on the
# project, evaluated PER REQUEST.
# ---------------------------------------------------------------------------

# What a route needs beyond the defaults is DECLARED ON THE ROUTE (or on the
# ``APIRouter`` that owns it), as a dependency that does nothing when called:
#
#     @router.get("/assist/hosts.ndjson",
#                 dependencies=[Depends(agent_read_floor(ProjectRole.AUDITOR))])
#     @router.post("/feedback", dependencies=[Depends(agent_session_metadata_write)])
#
# ``enforce_agent_operator_access`` reads the declaration off the matched route
# before the handler runs.  A route that declares nothing gets the defaults: a
# read needs membership (VIEWER), a write needs ANALYST.  The declaration must
# be on the route or in its router's constructor — ``include_router(...,
# dependencies=)`` is not part of the route object the gate is handed.

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: What a member may read unless the route says otherwise. Viewers can already
#: see hosts, scans and findings in the UI, so their agent may too.
_DEFAULT_READ_ROLE = ProjectRole.VIEWER

_ROLE_ORDER = (ProjectRole.VIEWER, ProjectRole.AUDITOR, ProjectRole.ANALYST, ProjectRole.ADMIN)


class _AgentRouteDeclaration:
    """A route's statement to the agent gate.  As a dependency it is a no-op;
    the gate finds it among the matched route's dependencies."""

    def __init__(self, *, read_floor: Optional[ProjectRole] = None,
                 session_metadata_write: bool = False):
        self.read_floor = read_floor
        self.session_metadata_write = session_metadata_write

    def __call__(self) -> None:
        return None


_READ_FLOORS = {role: _AgentRouteDeclaration(read_floor=role) for role in _ROLE_ORDER}


def agent_read_floor(role: ProjectRole) -> _AgentRouteDeclaration:
    """Declare the least project role whose agent may read this route — the
    role the equivalent page or export requires of a person, both ways: a bulk
    export or the Reports page is AUDITOR, Ingestion Results is ANALYST, and a
    file the UI serves to a viewer declares nothing.  Without it a viewer's
    agent would have data egress the viewer's own session is refused."""
    return _READ_FLOORS[role]


#: Declares a mutating route that records something about the SESSION (its end,
#: its key's deadline, feedback, a tool suggestion) rather than project data,
#: so a read-only operator's agent may call it.
agent_session_metadata_write = _AgentRouteDeclaration(session_metadata_write=True)


@dataclass(frozen=True)
class AgentRouteAccess:
    read_floor: ProjectRole
    session_metadata_write: bool


def agent_route_access(route) -> AgentRouteAccess:
    """What ``route`` declares to the agent gate; the defaults when it declares
    nothing.  Of several read floors (router and route), the strictest holds."""
    floor = _DEFAULT_READ_ROLE
    metadata_write = False
    for dep in getattr(route, "dependencies", None) or ():
        declared = getattr(dep, "dependency", None)
        if not isinstance(declared, _AgentRouteDeclaration):
            continue
        metadata_write = metadata_write or declared.session_metadata_write
        if declared.read_floor is not None and (
            _ROLE_ORDER.index(declared.read_floor) > _ROLE_ORDER.index(floor)
        ):
            floor = declared.read_floor
    return AgentRouteAccess(read_floor=floor, session_metadata_write=metadata_write)


def enforce_agent_operator_access(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
) -> Agent:
    """An agent key may do what its operator may do — checked on every request.

    v2.305.0.  The agent surface performed **zero** project-role checks: it had
    an entirely separate authorization model built on which workflow a key was
    scoped to. Two consequences, both real:

    * **Role changes did not reach live keys.** Demote an analyst to viewer, or
      remove them from the project, and their agent kept its old powers until
      the key expired. v2.304.0 made keys renewable, which widened that window
      rather than closing it.
    * It produced a whole bug class of its own — v2.90.3 fixed a viewer minting
      an agent key to bypass the analyst gate on the user-side plan routes. When
      the key carries the operator's role, that is unrepresentable rather than
      merely patched.

    Applied as a router-level dependency, so it covers every agent route without
    19 per-route edits and cannot be forgotten on a new one. Reads require
    current membership; writes additionally require ANALYST, except for the
    session-metadata writes above.

    A global admin bypasses, matching ``require_project_role``.
    """
    method = request.method.upper()
    declared = agent_route_access(request.scope.get("route"))
    is_write = method not in _READ_METHODS
    is_project_write = is_write and not declared.session_metadata_write

    # An archived project is closed to its agents as it is to its people
    # (``get_current_project`` answers 410): a key minted before the archive
    # kept reading and writing it (review 2026-09-23 R12).  Session metadata
    # writes — ending the session among them — still go through, so a
    # session can be wrapped up.
    # Everything below was read with the key (``_load_agent_key``): the gate
    # itself issues no statement.
    access = getattr(request.state, "_agent_operator_access", None)
    if access is None:
        raise HTTPException(status_code=401, detail="Invalid agent API key")
    project_archived, operator_id, operator_active, operator_is_admin, membership_role = access

    if (is_project_write or not is_write) and project_archived:
        raise HTTPException(status_code=410, detail="Project is archived")

    # ``operator_id`` is None only when the user row behind the key is gone,
    # which the schema does not allow for a live key; it is refused like an
    # inactive one.
    if operator_id is None or not operator_active:
        raise HTTPException(
            status_code=403,
            detail=(
                "This key's operator is no longer an active user. Ask an active "
                "project member to start a new session."
            ),
        )
    request.state.key_operator_is_admin = operator_is_admin
    if operator_is_admin:
        return agent

    if membership_role is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This key's operator is no longer a member of the project. The "
                "key cannot act on a project its operator has left."
            ),
        )
    request.state.key_operator_role = membership_role

    if is_project_write:
        if not check_permissions(membership_role, ProjectRole.ANALYST.value):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This key acts for a project {membership_role}, which is "
                    "read-only. An agent can only do what the operator who "
                    "started its session can do."
                ),
            )
        return agent

    # Reads: most need only membership, but bulk exports match their JWT
    # equivalents' floor.
    required_read = declared.read_floor
    if not check_permissions(membership_role, required_read.value):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This key acts for a project {membership_role}. This read "
                f"requires {required_read.value}, the same role the equivalent "
                "page or export requires of a person."
            ),
        )
    return agent


# v2.337.0 — ``require_plan_scope``, ``require_plan_generation_scope``,
# ``require_execution_session_scope``, ``require_recon_scope`` and
# ``require_assist_scope`` are gone.  They gated each agent router on the
# workflow a key was minted for; a key no longer has one.  What they were
# also doing — binding a call to its plan / scope / session — went with the
# things they bound to (recon runs v2.433.1; plans and execution runs
# v2.442.0): every write is project-scoped and carries the session's id.


# v2.295.0 — ``deny_scoped_keys`` is gone with the unscoped global key.  It
# admitted only keys whose workflow was None, which is now a rejected
# credential, so every endpoint behind it was unreachable by definition.  Its
# one consumer, ``POST /agent/test-plans``, went with it: plans are created by
# the operator (JWT) and filled in by the agent, which is what the MCP surface
# already assumed.


# ---------------------------------------------------------------------------
# Project access
# ---------------------------------------------------------------------------

def get_current_project(
    project_id: int = Path(..., description="Project ID", gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    """Validate that the project exists and the current user has access.

    Global admins can access any project. Other users must have a
    ProjectMembership row for the given project.

    Returns the Project instance for use in endpoint handlers.

    Plain ``def`` (not ``async``): the body does synchronous psycopg2
    queries, so FastAPI must run it in the threadpool — an ``async def``
    here blocked the worker's event loop for the project + membership
    round trips on every project-scoped request (code-review C3).
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


def require_project_role(required_role: "ProjectRole | str"):
    """Dependency factory that checks per-project role.

    Global admins bypass. Otherwise the user's ProjectMembership.role
    is checked against the role hierarchy.

    The argument is coerced to ``ProjectRole`` HERE, at factory-construction
    (import) time.  A typo'd role would otherwise be silent and dangerous:
    ``check_permissions`` looks the required role up in a hierarchy dict with
    ``.get(role, 0)``, so an unknown string yields required-level 0 and the
    gate passes for *everyone* (fails open).  Coercing raises ``ValueError``
    at import instead, so a bad role can never reach a request.
    """
    required = ProjectRole(required_role)

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

        if not check_permissions(membership.role, required.value):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient project role. Required: {required.value}",
            )
        return current_user

    return checker


async def read_upload_capped(
    file: UploadFile, max_bytes: int, *, detail: Optional[str] = None
) -> bytes:
    """Read an ``UploadFile`` fully, but abort the moment it exceeds ``max_bytes``.

    ``await file.read()`` with no size pulls the whole upload into Python memory.
    ``UploadFile`` may spool the request body to disk, but that unbounded read
    still materializes it — so a post-hoc ``len(content) > cap`` check runs too
    late: an authenticated multi-GB upload can OOM the container before the
    check. Reading in bounded chunks and rejecting at ``max_bytes + 1`` caps peak
    memory at roughly ``max_bytes`` regardless of the actual upload size.

    Raises 413 (Payload Too Large) when the cap is crossed; always closes the
    upload. Callers keep their own extension/content validation.
    """
    chunk_size = 64 * 1024
    buf = bytearray()
    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=detail
                    or f"Uploaded file exceeds the {max_bytes:,}-byte limit.",
                )
    finally:
        await file.close()
    return bytes(buf)


def resolve_project_assignee(
    db: Session, project_id: int, assignee_user_id: Optional[int]
) -> Optional[int]:
    """Validate that ``assignee_user_id`` may own/be-assigned work in this project.

    Returns the id unchanged when it's ``None`` (an explicit unassignment) or a
    valid target: an ACTIVE user who is a member of the project (global admins
    bypass the membership check, mirroring bulk_assign). Raises 400 for an
    inactive user, a non-existent user, or a user who isn't in the project — so
    every owner-write path (manual create, promotion, single update, bulk) shares
    one rule instead of accepting any global user id.
    """
    if assignee_user_id is None:
        return None
    assignee = (
        db.query(User)
        .filter(User.id == assignee_user_id, User.is_active.is_(True))
        .first()
    )
    if not assignee:
        raise HTTPException(status_code=400, detail="Assignee is not an active user")
    if assignee.role != UserRole.ADMIN:
        is_member = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == assignee.id,
            )
            .first()
        )
        if not is_member:
            raise HTTPException(
                status_code=400, detail="Assignee is not a member of this project"
            )
    return assignee_user_id
