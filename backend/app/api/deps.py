"""
Shared FastAPI dependencies for project-scoped endpoints.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Path, Request, UploadFile
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import CompileError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models import HostFollow
from app.db.models_auth import User, UserRole, APIKey
from app.db.models_agent import (
    Agent,
    AgentCapabilityConstraint,
    AgentRateBucket,
    LEGACY_WRITE_CAPABILITIES,
)
from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
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


def _as_utc(value):
    """Normalise a possibly tz-naive datetime to UTC-aware.

    Some drivers hand back naive datetimes even for ``DateTime(timezone=True)``
    columns; comparing one to an aware ``now()`` raises TypeError and 500s the
    request.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def session_renewal_deadline(agent_session) -> Optional[datetime]:
    """When this session stops being renewable — ``started_at`` + the cap.

    Returns None when there is no session to measure from, which makes the key
    non-renewable rather than immortal.
    """
    if agent_session is None:
        return None
    started = _as_utc(getattr(agent_session, "started_at", None))
    if started is None:
        return None
    return started + timedelta(hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS)


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


def _expired_key_detail(api_key_obj) -> Dict[str, object]:
    """The body of a 401 raised for an expired key.

    Structured because the caller is usually mid-workflow holding output it
    cannot reproduce cheaply, and "expired" vs "revoked" are the same status
    code but opposite situations. ``recoverable`` is the field an agent
    branches on.
    """
    session = getattr(api_key_obj, "agent_session", None)
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
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    api_key_obj = (
        db.query(APIKey)
        .options(joinedload(APIKey.agent_session))
        .filter(
            APIKey.key_hash == key_hash,
            APIKey.is_active.is_(True),
            APIKey.agent_id.isnot(None),
        )
        .first()
    )
    # A revoked key is gone for good — is_active is the operator's kill switch
    # and renewal must never route around it.
    if not api_key_obj:
        raise HTTPException(status_code=401, detail="Invalid or revoked agent API key")

    agent = (
        db.query(Agent)
        .filter(Agent.id == api_key_obj.agent_id, Agent.is_active.is_(True))
        .first()
    )
    if not agent:
        raise HTTPException(status_code=401, detail="Agent inactive or not found")

    if not key_is_renewable(api_key_obj.agent_session):
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
    session = api_key_obj.agent_session
    request.state.agent_session_id = session.id if session is not None else None
    # Per-workflow attribution, so a renewal lands on the same timeline as the
    # calls around it rather than as an orphan row.
    if session is not None:
        if session.workflow in ("plan_generation", "execution"):
            request.state.scoped_plan_id = session.plan_id
        elif session.workflow == "recon":
            request.state.scoped_scope_id = session.scope_id
    request.state.scoped_assist_session_id = api_key_obj.assist_session_id
    return api_key_obj


def resolve_capabilities(*, workflow, agent_session) -> tuple[frozenset, str | None]:
    """Return ``(capabilities, constraint)`` for an authenticated key.

    ``workflow`` says which surface a key may reach; capabilities say how
    much authority it carries there.  The two are deliberately separate so a
    write endpoint can state its own requirement positively instead of every
    handler re-deriving "…but not assist".

    Only assist sessions consult the stored ``capabilities`` column.  Plan,
    execution, recon, and legacy unscoped keys have always been able to write
    notes / follow state / execution results, so they resolve to
    :data:`LEGACY_WRITE_CAPABILITIES` unconditionally — the gate rolls out
    without changing what any existing key can do.  Assist sessions start at
    ``[]`` (read-only, today's behaviour) and only gain writes when the
    operator explicitly grants them at mint time.
    """
    if workflow != "assist":
        return LEGACY_WRITE_CAPABILITIES, None
    if agent_session is None:
        # Fail closed: an assist-scoped key with no session row to read the
        # grant from gets nothing.
        return frozenset(), None
    stored = agent_session.capabilities or []
    return frozenset(stored), agent_session.capability_constraint


def require_capability(capability: str):
    """Dependency factory: require *capability* on the calling agent key.

    Fails closed.  An endpoint that declares nothing grants nothing, so a
    newly-added write route cannot silently inherit authority it was never
    meant to have — the failure mode the previous per-handler deny guards
    had (forget the guard, and read-only keys could write).

    Pair with :func:`enforce_capability_row_scope` on host-targeted writes to
    apply the row-level constraint (e.g. "assigned hosts only").
    """
    def _dep(
        request: Request,
        agent: Agent = Depends(check_agent_rate_limit),
    ) -> Agent:
        caps = getattr(request.state, "key_capabilities", None) or frozenset()
        if capability not in caps:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This API key does not carry the '{capability}' capability. "
                    "Assist sessions are read-only unless the operator granted "
                    "write access when starting the session; start a new assist "
                    "session with write access enabled, or use the UI."
                ),
            )
        return agent
    return _dep


def enforce_capability_row_scope(
    request: Request, db: Session, *, host_id: int
) -> None:
    """Apply the key's row-level constraint to a host-targeted write.

    No constraint (plan / recon / legacy keys) is a no-op.  Under
    ``ASSIGNED``, the host must be assigned to the session's own operator —
    a ``host_follows`` row for ``started_by_id`` with ``assigned_at`` set,
    which is exactly what the ``assigned:me`` host filter resolves.  Raises
    403 otherwise.
    """
    constraint = getattr(request.state, "key_capability_constraint", None)
    if constraint != AgentCapabilityConstraint.ASSIGNED.value:
        return

    operator_id = getattr(request.state, "key_operator_id", None)
    if operator_id is None:
        # The grant is meaningless without an operator to resolve it against.
        raise HTTPException(
            status_code=403,
            detail=(
                "This key's write access is limited to hosts assigned to the "
                "operator who started the session, but no operator is bound to "
                "it. Start a new session."
            ),
        )

    assigned = (
        db.query(HostFollow.id)
        .filter(
            HostFollow.host_id == host_id,
            HostFollow.user_id == operator_id,
            HostFollow.assigned_at.isnot(None),
        )
        .first()
    )
    if assigned is None:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host {host_id} is not assigned to you. This session may only "
                "write to hosts assigned to the operator who started it — ask a "
                "project lead to assign the host, or make the change in the UI."
            ),
        )


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
        # Eager-load the bound AgentSession — the workflow discriminator
        # below reads it on every authenticated /agent/* request (the
        # chattiest authenticated path), so a lazy load here would add a
        # round-trip per call once keys carry an agent_session_id.
        .options(joinedload(APIKey.agent_session))
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
            # v2.304.0 — a structured 401, because "expired" and "revoked" are
            # the same status code but completely different situations for the
            # caller, and the caller is usually holding hours of scan output at
            # this point.  Flat prose gave it no way to tell whether retrying
            # was worth anything.  ``recoverable`` says: renew with THIS key and
            # try again.
            raise HTTPException(
                status_code=401,
                detail=_expired_key_detail(api_key_obj),
            )

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
            # Fail CLOSED: the key is bound to a session whose workflow this
            # code can't classify (data corruption, or a new workflow added
            # to the enum without teaching the guards about it).  Falling
            # through to key_workflow=None would treat it as an unscoped
            # global key — the most-privileged outcome — so deny outright.
            logger.warning(
                "agent key %s bound to agent_session %s with unrecognized "
                "workflow %r — denying",
                api_key_obj.key_prefix, agent_session.id, _wf,
            )
            raise HTTPException(
                status_code=403,
                detail="API key is bound to an unrecognized workflow; regenerate it.",
            )
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
        # v2.295.0 — unscoped ("global") agent keys are abolished.  A key with
        # no session and no legacy scope column used to be the MOST privileged
        # agent credential in the system: it reached every plan in the project
        # (require_plan_scope let it through), passed deny_scoped_keys, and
        # resolved to LEGACY_WRITE_CAPABILITIES.  Its only mint paths were
        # POST /agents/ and POST /agents/{id}/rotate-key, both removed with the
        # /agents router; every surviving mint path binds an AgentSession.
        #
        # Fail CLOSED rather than merely stopping the minting: an orphaned
        # legacy key would otherwise keep full authority with no UI and no API
        # left to rotate or revoke it.  Deployments holding one must start a
        # session from the workflow's own page to get a scoped key.
        logger.warning(
            "rejecting unscoped agent key %s (agent_id=%s) — global keys were "
            "removed in v2.295.0; start a workflow session to mint a scoped key",
            api_key_obj.key_prefix, api_key_obj.agent_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "This is an unscoped global agent key. Those were removed — "
                "every key is now bound to one workflow session. Start a "
                "plan-generation, execution, recon, or assist session from "
                "the project UI to mint a scoped key."
            ),
        )
    request.state.agent_session_id = (
        agent_session.id if agent_session is not None else None
    )

    # Effective authority for this key.  Read access is implicit; only writes
    # are enumerated.  See ``resolve_capabilities`` for why non-assist
    # workflows don't consult the stored column.
    caps, constraint = resolve_capabilities(
        workflow=request.state.key_workflow, agent_session=agent_session
    )
    request.state.key_capabilities = caps
    request.state.key_capability_constraint = constraint
    # The human this session acts on behalf of.  Row-level constraints
    # ("assigned to me") resolve against this, and agent-authored notes are
    # attributed to them with actor_type='agent'.
    request.state.key_operator_id = (
        agent_session.started_by_id if agent_session is not None else None
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
    if need_commit:
        db.commit()

    return agent


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
        db.commit()
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

# Paths here are **router-relative**, which is what ``request.scope["route"].path``
# returns — FastAPI 0.141 keeps included routers as a single node, so the matched
# route object is the one registered on the sub-router and carries its own path,
# not the mounted `/api/v1/agent/...` one. v2.307.0 fixed exactly this: the
# entries were written as full paths, matched nothing, and every one of these
# routes was silently gated as a project write — so a read-only operator could
# not report an environment probe or file feedback. Nothing failed loudly,
# because the failure direction is a 403 that looks deliberate.
#
# ``tests/test_agent_operator_access.py`` pins each entry to exactly one mounted
# route, so a renamed path can't quietly drop out of the allowlist again.
AGENT_SESSION_METADATA_WRITES = frozenset({
    # Mounted outside this gate entirely (its own router, so an expired key can
    # reach it). Listed for completeness — if it were ever moved back under the
    # gate, it must not become a project write.
    ("POST", "/session/renew"),
    ("POST", "/assist/sessions/{session_id}/environment"),
    ("POST", "/execution-sessions/{session_id}/environment"),
    ("POST", "/recon/sessions/{session_id}/environment"),
    ("POST", "/feedback"),
    ("POST", "/tool-suggestions"),
})

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def enforce_agent_operator_access(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
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
    route = request.scope.get("route")
    path = getattr(route, "path", "") or ""
    is_write = method not in _READ_METHODS
    is_project_write = is_write and (method, path) not in AGENT_SESSION_METADATA_WRITES

    # Prefer the session's own starter; fall back to the agent's owner.
    #
    # These are the same person in practice — an Agent is unique per
    # (user, project) and a session is started by the user whose agent it is —
    # but they fail differently. ``started_by_id`` is ON DELETE SET NULL and is
    # absent on keys minted before the unified session binding, whereas
    # ``Agent.owner_id`` is set at creation and always present. Reading only the
    # session would make this gate deny keys whose operator is perfectly
    # identifiable, which is a worse answer than the one it replaces.
    operator_id = getattr(request.state, "key_operator_id", None) or agent.owner_id
    if operator_id is None:
        # Both gone: the operator's account was deleted, which is deliberately
        # non-destructive to the audit trail. Reads continue; writes stop,
        # because there is no longer anyone whose authority this key acts under.
        if is_project_write:
            logger.warning(
                "agent key %s has no resolvable operator — refusing %s %s",
                getattr(request.state, "api_key_prefix", "?"), method, path,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    "This key's operator no longer exists, so it cannot write. "
                    "Ask an active project member to start a new session."
                ),
            )
        return agent

    operator = db.query(User).filter(User.id == operator_id).first()
    if operator is None or not operator.is_active:
        raise HTTPException(
            status_code=403,
            detail=(
                "This key's operator is no longer an active user. Ask an active "
                "project member to start a new session."
            ),
        )
    request.state.key_operator_is_admin = operator.role == UserRole.ADMIN
    if operator.role == UserRole.ADMIN:
        return agent

    membership = (
        db.query(ProjectMembership)
        .filter(
            ProjectMembership.project_id == agent.project_id,
            ProjectMembership.user_id == operator.id,
        )
        .first()
    )
    if membership is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This key's operator is no longer a member of the project. The "
                "key cannot act on a project its operator has left."
            ),
        )
    request.state.key_operator_role = membership.role

    if is_project_write and not check_permissions(membership.role, ProjectRole.ANALYST.value):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This key acts for a project {membership.role}, which is "
                "read-only. An agent can only do what the operator who started "
                "its session can do."
            ),
        )
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
    request ``plan_id`` must match — otherwise 403.

    v2.295.0 — unscoped global keys no longer reach this check at all;
    they are rejected during authentication.  This guard previously let
    them through to *every* plan in the project.

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
    # Stash the key's agent project so _load_assist_session can re-assert
    # session.project_id == agent.project_id (defence-in-depth against a
    # hand-edited api_keys row pairing an agent with another project's
    # session — mirrors the recon loader's scope_id re-check).
    request.state.scoped_agent_project_id = agent.project_id
    return agent


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
