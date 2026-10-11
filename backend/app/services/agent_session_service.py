"""
Agent sessions — lifecycle helpers and the unified timeline read path.

v2.337.0 — one project-scoped session per operator replaces the four
per-workflow entry points.  This module owns:

* **Lifecycle**: creating a session, minting / re-minting its key, ending a
  session, and lapsing sessions whose keys have all expired.  (A session had
  "phases" — the recon and execution runs it opened — until v2.433.0 and
  v2.442.0; it now simply proposes host tests and records evidence.)
* **The session list**: what the Agent Sessions page and Operations read.

**One table, one id, no kinds.**  A session is a row of ``agent_sessions`` and
every route, page and link is keyed by that row's id (v2.449.0; the
``assist_sessions`` table and, in 2.489.0, the last column and routes that
remembered its ids are gone).  Every listed session READS the same: no answer
carries a kind, and nothing here computes anything for one kind only (owner,
2026-10-10).  The stored ``agent_sessions.workflow`` is read for one thing in
this module — which rows the list has (:data:`LISTED_WORKFLOWS`).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Annotation
from app.db.models_agent import (
    Agent,
    AgentApiCall,
    AgentFeedback,
    AgentSession,
    AgentSessionWorkflow,
)
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.services.agent_prompt_service import PROMPT_VERSION
from app.services.agent_key_ttl import resolve_expires_at, session_renewal_deadline

logger = logging.getLogger(__name__)


#: The stored ``agent_sessions.workflow`` values whose rows are sessions the
#: list has and ``/agent-sessions/{id}`` answers for: every session started
#: since v2.337.0 (``project``, the only value written) and the assist
#: sessions from before it.  Rows stored as ``recon`` / ``plan_generation`` /
#: ``execution`` (what they did went with recon runs, test plans and execution
#: runs) stay on no page, as before.  This tuple is the ONE place that says so;
#: nothing is displayed, labelled or counted by the value.
LISTED_WORKFLOWS = (
    AgentSessionWorkflow.PROJECT.value,
    AgentSessionWorkflow.ASSIST.value,
)

#: Session statuses.  ``active`` is the only one a key works under.
SESSION_ACTIVE = "active"
SESSION_ENDED = "ended"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def create_agent_session(
    db: Session,
    *,
    project_id: int,
    agent_id: Optional[int],
    started_by_id: Optional[int],
    purpose: Optional[str] = None,
    workflow: str = AgentSessionWorkflow.PROJECT.value,
    status: str = SESSION_ACTIVE,
) -> AgentSession:
    """Create + flush an ``AgentSession`` and return it.

    ``workflow`` defaults to ``project``; the legacy values are accepted only
    so tests and backfills can construct historical shapes.  A session carries
    no single target.
    """
    base = AgentSession(
        workflow=workflow,
        project_id=project_id,
        agent_id=agent_id,
        started_by_id=started_by_id,
        purpose=(purpose or "").strip() or None,
        status=status,
        # v2.434.0 — the server issued this prompt, so it records the version
        # itself (the environment probe used to carry the agent's claim of it).
        prompt_version=PROMPT_VERSION,
    )
    db.add(base)
    db.flush()
    return base


def resolve_project_agent(
    db: Session,
    *,
    project_id: int,
    user: User,
    prefer_agent_id: Optional[int] = None,
) -> Agent:
    """The per-(user, project) ``Agent`` row, auto-provisioned if missing.

    One implementation for what used to be three copies (recon, plan,
    assist).  Prefers ``prefer_agent_id`` (a resumed session's original
    agent), then the user's own agent on the project.  Reactivates a
    deactivated agent rather than minting a second identity.
    """
    agent: Optional[Agent] = None
    if prefer_agent_id is not None:
        agent = db.query(Agent).filter(Agent.id == prefer_agent_id).first()
    if agent is None:
        agent = (
            db.query(Agent)
            .filter(Agent.project_id == project_id, Agent.owner_id == user.id)
            .first()
        )
    if agent is not None:
        if not agent.is_active:
            agent.is_active = True
        return agent
    agent = Agent(
        name=f"{user.username}-agent",
        project_id=project_id,
        owner_id=user.id,
        description="Auto-provisioned for agent sessions",
    )
    db.add(agent)
    db.flush()
    return agent


def mint_session_key(
    db: Session,
    *,
    agent: Agent,
    session: AgentSession,
    ttl_hours: Optional[int] = None,
    name: Optional[str] = None,
) -> str:
    """Mint a fresh key bound to ``session``; return the plaintext once.

    Revokes any prior active key on the same session first — one live key
    per session, ever, so a resumed session's orphaned key cannot keep
    writing beside the new one.  ``uq_api_key_agent_session_active`` is the
    DB backstop; a race on it surfaces as 409 rather than a bare 500.

    The key never outlives the session: its expiry is cut to the session's
    renewal deadline (``session_renewal_deadline`` — start + the lifetime cap)
    HERE, in the one mint, as a renewal's is.  A Resume an hour before the cap
    used to hand out a key good for the whole requested TTL, which nothing
    afterwards compared with the deadline.
    """
    expires_at = resolve_expires_at(ttl_hours)
    deadline = session_renewal_deadline(session)
    if deadline is not None and expires_at > deadline:
        expires_at = deadline

    db.query(APIKey).filter(
        APIKey.agent_session_id == session.id,
        APIKey.is_active.is_(True),
    ).update({"is_active": False}, synchronize_session=False)

    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    db.add(
        APIKey(
            agent_id=agent.id,
            agent_session_id=session.id,
            name=name or f"agent-session-{session.id}",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=raw_key[:14],
            expires_at=expires_at,
            # The application's clock, not the database's: the auth chain
            # compares this with ``User.password_changed_at``, which the
            # password routes also stamp from the application.
            created_at=datetime.now(timezone.utc),
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another key became active for this session concurrently. Retry.",
        ) from exc
    return raw_key


def revoke_session_keys(db: Session, session_id: int) -> int:
    return (
        db.query(APIKey)
        .filter(APIKey.agent_session_id == session_id, APIKey.is_active.is_(True))
        .update({"is_active": False}, synchronize_session=False)
    )





# Column widths of the attribution fields (``generated_by_model`` /
# ``generated_by_tool``), so a long self-report is cut, not refused.
_ATTRIBUTION_MAX = 100


def note_agent_model(session: Optional[AgentSession], model: Optional[str]) -> None:
    """Record the model the agent says it is running as (v2.434.0).

    Self-reported and optional — no protocol carries the model — on the writes
    where it matters (proposing host tests, a proposal, ending the session).
    The session keeps the LAST one reported: a session can switch models
    mid-way, and each test and evidence record snapshots the value current
    when it was written.
    """
    model = (model or "").strip()
    if session is not None and model:
        session.generated_by_model = model[:_ATTRIBUTION_MAX]


def note_agent_harness(session: Optional[AgentSession], harness: Optional[str], *, overwrite: bool) -> None:
    """Record the client the agent runs in (v2.434.0).

    From the MCP ``initialize`` handshake's ``clientInfo`` (``overwrite=True``:
    the client names itself) or, failing that, the first call's
    ``User-Agent`` (``overwrite=False``: a fallback never replaces a name).
    """
    harness = (harness or "").strip()
    if session is None or not harness:
        return
    if overwrite or not session.generated_by_tool:
        session.generated_by_tool = harness[:_ATTRIBUTION_MAX]


def record_mcp_client(raw_key: Optional[str], client_info: Optional[dict]) -> None:
    """Name the session's harness from an MCP ``initialize`` (v2.434.0).

    ``initialize`` needs no key, so this only acts when the client sent one
    that is live.  Its own DB session, and it never raises: attribution must
    not be able to fail a handshake.
    """
    import hashlib

    from app.db import session as _session_module

    if not raw_key or not isinstance(client_info, dict):
        return
    name = client_info.get("name")
    version = client_info.get("version")
    if not isinstance(name, str) or not name.strip():
        return
    harness = f"{name.strip()} {version.strip()}" if isinstance(version, str) and version.strip() else name.strip()
    db: Session = _session_module.SessionLocal()
    try:
        session = (
            db.query(AgentSession)
            .join(APIKey, APIKey.agent_session_id == AgentSession.id)
            .filter(
                APIKey.key_hash == hashlib.sha256(raw_key.encode()).hexdigest(),
                APIKey.is_active.is_(True),
                AgentSession.status == SESSION_ACTIVE,
            )
            .first()
        )
        if session is not None and session.generated_by_tool != harness[:_ATTRIBUTION_MAX]:
            note_agent_harness(session, harness, overwrite=True)
            db.commit()
    except Exception:
        logger.exception("recording the MCP client on its session failed")
        db.rollback()
    finally:
        db.close()



#: v2.343.0 — the three ways a session ends, stored on ``AgentSession.end_reason``
#: so clean exits can be counted against abandoned ones.
END_REASON_AGENT = "agent"        # POST /agent/session/end — the clean exit
END_REASON_OPERATOR = "operator"  # End on Agent Runs / the sessions panel
END_REASON_LAPSED = "lapsed"      # the hourly sweep, past the renewal window


def end_agent_session(
    db: Session,
    session: AgentSession,
    *,
    ended_by: Optional[User] = None,
    reason: Optional[str] = None,
    end_reason: Optional[str] = None,
) -> None:
    """End a session: revoke its keys.

    Nothing else needs closing (v2.442.0): the host tests it proposed and the
    evidence it recorded are project data, not session state, and any later
    session — or a person — carries them on.

    ``end_reason`` is the typed classification (``END_REASON_*``); when it is
    not given, a caller that names a person is an operator end and anything
    else is the sweep, which keeps the two pre-existing callers correct.
    """
    now = datetime.now(timezone.utc)
    who = (ended_by.full_name or ended_by.username) if ended_by is not None else "system"
    line = f"[{now.isoformat()}] Session ended by {who}" + (f": {reason}" if reason else "")

    revoke_session_keys(db, session.id)
    session.status = SESSION_ENDED
    session.completed_at = now
    session.end_reason = end_reason or (
        END_REASON_OPERATOR if ended_by is not None else END_REASON_LAPSED
    )
    session.notes = (f"{session.notes}\n{line}" if session.notes else line)[-8192:]


def end_sessions_of_operator(
    db: Session, user_id: int, *, ended_by: Optional[User], reason: str,
) -> int:
    """End every active session ``user_id`` started, in every project.

    For when the account's credentials are replaced (password change or reset,
    two-factor reset): a session opened under the old credentials may have been
    opened by whoever had them.  Each session is ended like an operator's End,
    so the Agent Sessions page shows it ended with ``reason``.  Any other live
    key of the user's agents is revoked too.  Returns the number of sessions
    ended; the caller commits.
    """
    sessions = (
        db.query(AgentSession)
        .filter(
            AgentSession.started_by_id == user_id,
            AgentSession.status == SESSION_ACTIVE,
        )
        .all()
    )
    for session in sessions:
        end_agent_session(
            db, session, ended_by=ended_by, reason=reason, end_reason=END_REASON_OPERATOR,
        )
    owned_agents = db.query(Agent.id).filter(Agent.owner_id == user_id)
    db.query(APIKey).filter(
        APIKey.is_active.is_(True),
        APIKey.agent_id.in_(owned_agents),
    ).update({"is_active": False}, synchronize_session=False)
    return len(sessions)


def feedback_counts_for_agent_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{agent_session_id: feedback rows}`` for the given sessions, one query."""
    if not session_ids:
        return {}
    rows = (
        db.query(AgentFeedback.agent_session_id, func.count(AgentFeedback.id))
        .filter(AgentFeedback.agent_session_id.in_(session_ids))
        .group_by(AgentFeedback.agent_session_id)
        .all()
    )
    return {sid: int(n) for sid, n in rows}


def operator_role(global_role, membership_role: Optional[str]) -> Optional[str]:
    """The authority a session's operator carries in this project (v2.402.0).

    Mirrors ``enforce_agent_operator_access``: a global admin passes whatever
    their membership says, so unless that membership already reads admin the
    honest label is ``global_admin``; otherwise the membership role; otherwise
    None (not a member — the key's next call is refused).
    """
    is_global_admin = global_role in (UserRole.ADMIN, UserRole.ADMIN.value)
    if is_global_admin and membership_role != ProjectRole.ADMIN.value:
        return "global_admin"
    return membership_role or None


@dataclass
class SessionActivity:
    """What the audit log says about one session."""
    call_count: int = 0
    via_mcp: bool = False
    first_call_at: Optional[datetime] = None

    @property
    def connection(self) -> str:
        """How the agent reached the session, from observed calls (v2.331.0):
        ``none`` — no authenticated call yet; ``mcp`` — at least one arrived
        through the MCP transport; ``curl`` — calls arrived, all by direct
        HTTP.  Not a liveness claim: a past call says the client connected,
        not that it is still running."""
        if self.call_count == 0:
            return "none"
        return "mcp" if self.via_mcp else "curl"


def activity_for_agent_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{agent_session_id: SessionActivity}`` — audited call count, transport
    and first-call time per session, one grouped query.

    Every audited row is an authenticated call (the middleware writes nothing
    for a request that never authenticated), so one row is proof the key was
    accepted, and ``via_mcp`` on any of them proof the MCP transport carried it.
    Rides ``idx_agent_api_call_session_created``.
    """
    if not session_ids:
        return {}
    mcp_seen = func.max(case((AgentApiCall.via_mcp.is_(True), 1), else_=0))
    return {
        sid: SessionActivity(call_count=int(count), via_mcp=bool(via_mcp), first_call_at=first_at)
        for sid, count, via_mcp, first_at in (
            db.query(
                AgentApiCall.agent_session_id,
                func.count(AgentApiCall.id),
                mcp_seen,
                func.min(AgentApiCall.created_at),
            )
            .filter(AgentApiCall.agent_session_id.in_(session_ids))
            .group_by(AgentApiCall.agent_session_id)
            .all()
        )
    }


def note_counts_for_agent_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{agent_session_id: notes it wrote}``, one grouped query."""
    if not session_ids:
        return {}
    return {
        sid: int(n)
        for sid, n in (
            db.query(Annotation.agent_session_id, func.count(Annotation.id))
            .filter(Annotation.agent_session_id.in_(session_ids))
            .group_by(Annotation.agent_session_id)
            .all()
        )
    }


def _operator_roles(db: Session, project_id: int, user_ids: set) -> dict:
    """``{user_id: operator role}`` for the operators on a page, one query."""
    ids = [u for u in user_ids if u is not None]
    if not ids:
        return {}
    rows = (
        db.query(User.id, User.role, ProjectMembership.role)
        .outerjoin(
            ProjectMembership,
            (ProjectMembership.user_id == User.id)
            & (ProjectMembership.project_id == project_id),
        )
        .filter(User.id.in_(ids))
        .all()
    )
    return {uid: operator_role(global_role, member_role) for uid, global_role, member_role in rows}


def get_agent_session_row(
    db: Session, project_id: int, session_id: int,
) -> Optional[AgentSessionRow]:
    """One session as the list shows it (work counts, key state, activity,
    feedback, operator role), or None when it is not this project's or not a
    session the list has (:data:`LISTED_WORKFLOWS`).
    """
    rows = list_agent_sessions(db, project_id, session_id=session_id, limit=1)
    return rows[0] if rows else None


def key_expiry_for_agent_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{agent_session_id: max(expires_at)}`` over active keys, one query.

    Scoped to the page's ids rather than the deployment's history.  MAX because
    a session can hold more than one key row (a re-mint on resume) and access
    stops when the LAST one dies — the earliest would call a usable session
    dead.  The one key-expiry read (``assist_session_service`` had a second,
    keyed by the pointer row's id).
    """
    if not session_ids:
        return {}
    return {
        sid: expires_at
        for sid, expires_at in (
            db.query(APIKey.agent_session_id, func.max(APIKey.expires_at))
            .filter(
                APIKey.agent_session_id.in_(session_ids),
                APIKey.is_active.is_(True),
            )
            .group_by(APIKey.agent_session_id)
            .all()
        )
    }


# v2.340.0 — ``effective_session_status`` deleted.  It flipped a displayed
# status to ``ended`` the moment the key expired, which renewal (v2.304.0) made
# wrong: an expired key on a session under its lifetime cap is still usable
# after one ``/session/renew`` call, so that session is *resumable*, not over.
# Nothing called it.  The timeline now carries ``key_expires_at`` +
# ``renewable_until`` instead, and the UI says "key expired, renewable until …"
# on an otherwise-active row — which is the truthful state.


def session_key_state(
    status, key_expires_at: Optional[datetime], renewable_until: Optional[datetime],
    now: Optional[datetime] = None,
) -> str:
    """Where a session stands, from the three facts the session list carries
    (``status``, ``key_expires_at``, ``renewable_until``) — the server's twin
    of the Agent Sessions page's Live / Resumable / Expired:

    * ``live``      — active, and a key is valid right now;
    * ``resumable`` — active, no valid key, still inside its lifetime (the
      operator can resume it: same session, a new key);
    * ``ended``     — anything else: ended, or active on paper with no key
      and past its lifetime.

    For a line of text that would otherwise print the stored status: "active"
    alone cannot tell a connected agent from a session whose key ran out a
    day ago.
    """
    if getattr(status, "value", status) != "active":
        return "ended"
    now = now or datetime.now(timezone.utc)

    def _after_now(t: Optional[datetime]) -> bool:
        if t is None:
            return False
        return (t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)) > now

    if _after_now(key_expires_at):
        return "live"
    if _after_now(renewable_until):
        return "resumable"
    return "ended"


def resume_agent_session(
    db: Session,
    session: AgentSession,
    *,
    agent: Agent,
    resumed_by: User,
    ttl_hours: Optional[int] = None,
) -> str:
    """Rotate the key on an active session so the operator can reconnect.

    v2.340.0.  The usual reason a session shows ``active`` with nothing
    happening is that the client that drove it died mid-tool (an editor agent
    session timed out while a scan ran) — the session and its
    key are all still good, only the process holding the key is gone.  The
    operator either still has the key configured (then nothing needs minting;
    they reopen the client) or has lost it, in which case this mints a
    replacement on the **same** session: ``mint_session_key`` revokes the
    previous key first, so the dead client cannot keep writing beside the new
    one.  It is the only resume (v2.433.0).

    Refuses (409) a session that is not active or is past its renewal deadline:
    past the cap nothing can be renewed, so the honest answer is "start a new
    session".
    """
    now = datetime.now(timezone.utc)
    if session.status != SESSION_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"Session is '{session.status}', not active — start a new session instead.",
        )
    deadline = session_renewal_deadline(session)
    if deadline is not None and deadline <= now:
        raise HTTPException(
            status_code=409,
            detail=(
                "Session is past its maximum lifetime and can no longer be resumed — "
                "start a new session instead."
            ),
        )
    raw_key = mint_session_key(db, agent=agent, session=session, ttl_hours=ttl_hours)
    if session.agent_id != agent.id:
        session.agent_id = agent.id
    # The resumed agent is handed the CURRENT prompt.  Its client and model
    # are updated as the new process reports them (handshake, self-report).
    session.prompt_version = PROMPT_VERSION
    who = resumed_by.full_name or resumed_by.username
    line = f"[{now.isoformat()}] Session resumed by {who}: key rotated, previous key revoked"
    session.notes = (f"{session.notes}\n{line}" if session.notes else line)[-8192:]
    return raw_key


def close_agent_session_from_agent(
    db: Session, session: AgentSession, *, notes: Optional[str] = None,
) -> None:
    """The agent's own exit: end the session it is holding the key for.

    v2.340.0.  Until now only the operator (End on Agent Activity) or the
    hourly sweep — after the key had expired *and* the session had passed its
    lifetime cap, a week by default — could end a project session, so
    even an agent that finished cleanly left an active row behind.
    """
    if session.status != SESSION_ACTIVE:
        raise HTTPException(
            status_code=409, detail=f"Session already in state '{session.status}'.",
        )
    reason = "closed by the agent" + (f": {notes.strip()}" if notes and notes.strip() else "")
    end_agent_session(db, session, ended_by=None, reason=reason, end_reason=END_REASON_AGENT)


def lapse_expired_agent_sessions(db: Session) -> int:
    """End every active session whose keys have all expired past renewal.

    A key can be renewed after expiry while its session is under the maximum
    lifetime (v2.304.0), so a session is lapsed only once its renewal
    deadline has also passed — otherwise the sweep would kill a session whose
    agent is mid-scan and about to renew.  Returns the number ended.
    """
    now = datetime.now(timezone.utc)
    live_expiry = (
        db.query(
            APIKey.agent_session_id.label("session_id"),
            func.max(APIKey.expires_at).label("expires_at"),
        )
        .filter(APIKey.is_active.is_(True))
        .group_by(APIKey.agent_session_id)
        .subquery()
    )
    rows = (
        db.query(AgentSession, live_expiry.c.expires_at)
        .outerjoin(live_expiry, live_expiry.c.session_id == AgentSession.id)
        .filter(AgentSession.status == SESSION_ACTIVE)
        .all()
    )
    lapsed: List[AgentSession] = []
    for session, expires_at in rows:
        if expires_at is not None:
            exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
            if exp > now:
                continue  # still usable
        deadline = session_renewal_deadline(session)
        if deadline is not None and deadline > now:
            continue  # expired, but still renewable with the same key
        end_agent_session(
            db, session,
            reason="keys expired past the renewal window",
            end_reason=END_REASON_LAPSED,
        )
        # The truthful timestamp is when access actually stopped (the key's
        # expiry), not when the sweep happened to run.
        session.completed_at = expires_at or session.completed_at
        lapsed.append(session)
    if lapsed:
        db.commit()
        logger.info(
            "Lapsed %d agent session(s) whose keys had expired: %s",
            len(lapsed), ", ".join(f"#{s.id}" for s in lapsed[:20]),
        )
    return len(lapsed)


@dataclass
class AgentSessionRow:
    """One row of the session list.

    ``status`` is the stored status (no normalisation — the UI maps it to a
    presentation palette).  Every row carries the same fields, computed the
    same way: there is no kind.
    """
    id: int
    project_id: int
    agent_id: Optional[int]
    user_id: Optional[int]
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    generated_by_model: Optional[str]
    generated_by_tool: Optional[str]
    prompt_version: Optional[str]
    # Denormalised display labels — populated by joining agents/users
    # at the service layer so the UI doesn't have to round-trip.
    agent_name: Optional[str] = None
    user_username: Optional[str] = None
    # The label a script gave the session at start, if any.
    purpose: Optional[str] = None
    # v2.340.0 — when the session's live key stops working and until when the
    # session can still be renewed / resumed.  An ``active`` row whose key has
    # expired but is still renewable is a session the operator can reconnect
    # to, and the page needs both dates to say so.  These two with ``status``
    # are the whole of "can this session still act" (``session_key_state``).
    key_expires_at: Optional[datetime] = None
    renewable_until: Optional[datetime] = None
    # v2.343.0 — how the session ended (END_REASON_*; None while active) and
    # how many feedback submissions it made.
    end_reason: Optional[str] = None
    feedback_count: int = 0
    # v2.402.0 — the operator's display name (users.full_name), shown in
    # preference to the username; None when the account has none.
    user_full_name: Optional[str] = None
    # Whether the Resume route takes this row at all (it refuses a row not
    # stored as ``project``).  Not an answer field: it only feeds the wire's
    # ``can_resume``, which must mirror the route.
    resume_supported: bool = True
    # v2.432.0 — ``last_activity_at`` is the session's most recent
    # authenticated call; ``operator_role`` the authority it acts with
    # (:func:`operator_role`).
    # v2.442.0 — ``host_test_count`` / ``evidence_count``: the host tests the
    # session proposed and the evidence records it wrote (the work a session
    # leaves behind; they replace ``phases``, the runs it used to open).
    host_test_count: int = 0
    evidence_count: int = 0
    last_activity_at: Optional[datetime] = None
    operator_role: Optional[str] = None
    # v2.449.0 — how much the session did, from the audit log and its notes
    # (they were on the pointer row's review routes): a session that made no
    # call is the common dead end (key minted, prompt never pasted) and must be
    # tellable from one that did the work without opening each.
    call_count: int = 0
    note_count: int = 0
    connection: str = "none"   # none | mcp | curl — see SessionActivity
    first_call_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "generated_by_model": self.generated_by_model,
            "generated_by_tool": self.generated_by_tool,
            "prompt_version": self.prompt_version,
            "agent_name": self.agent_name,
            "user_username": self.user_username,
            "purpose": self.purpose,
            "key_expires_at": self.key_expires_at,
            "renewable_until": self.renewable_until,
            "end_reason": self.end_reason,
            "feedback_count": self.feedback_count,
            "user_full_name": self.user_full_name,
            "host_test_count": self.host_test_count,
            "evidence_count": self.evidence_count,
            "last_activity_at": self.last_activity_at,
            "operator_role": self.operator_role,
            "call_count": self.call_count,
            "note_count": self.note_count,
            "connection": self.connection,
            "first_call_at": self.first_call_at,
        }


def sessions_with_a_page(db: Session, session_ids) -> set:
    """Which of these session ids the list has — the ones
    ``/agent-sessions/{id}`` answers for (:data:`LISTED_WORKFLOWS`).  A link to
    a session should be offered only for these.  One query."""
    ids = [i for i in session_ids if i is not None]
    if not ids:
        return set()
    return {
        sid
        for (sid,) in db.query(AgentSession.id).filter(
            AgentSession.id.in_(ids),
            AgentSession.workflow.in_(LISTED_WORKFLOWS),
        )
    }


def _apply_session_filters(q, *, agent_id, model, tool, user_id, status):
    """The list's one filter set: the rows the list has, narrowed by what the
    caller asked for.  The list, its count and the by-model rollup all end
    here, so they cannot disagree about which rows are sessions."""
    q = q.filter(AgentSession.workflow.in_(LISTED_WORKFLOWS))
    if agent_id is not None:
        q = q.filter(AgentSession.agent_id == agent_id)
    if model is not None:
        q = q.filter(AgentSession.generated_by_model == model)
    if tool is not None:
        q = q.filter(AgentSession.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(AgentSession.started_by_id == user_id)
    if status is not None:
        q = q.filter(AgentSession.status == status)
    return q


def count_agent_sessions(
    db: Session,
    project_id: int,
    *,
    agent_id: Optional[int] = None,
    model: Optional[str] = None,
    tool: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
) -> int:
    """The number of sessions the same filters would list, as one
    ``SELECT COUNT(*)`` — never ``len()`` of a fetched page, which silently
    under-reports on a long-lived project (v2.43.3, AUD-O1)."""
    q = db.query(func.count(AgentSession.id)).filter(AgentSession.project_id == project_id)
    q = _apply_session_filters(
        q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status,
    )
    return int(q.scalar() or 0)


def _status_value(status) -> str:
    """A status column as its string (some are enum columns, some strings)."""
    return status.value if hasattr(status, "value") else str(status)


def _aware_utc(t: Optional[datetime]) -> Optional[datetime]:
    """Read a tz-naive timestamp as UTC so mixed columns sort together."""
    return t if t is None or t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _attach_work_counts(db: Session, rows: "List[AgentSessionRow]") -> None:
    """Set ``host_test_count`` / ``evidence_count`` on every row (v2.442.0):
    two grouped queries whatever the page size."""
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    ids = [r.id for r in rows]
    if not ids:
        return
    tests = dict(
        db.query(HostTest.agent_session_id, func.count(HostTest.id))
        .filter(HostTest.agent_session_id.in_(ids))
        .group_by(HostTest.agent_session_id)
        .all()
    )
    evidence = dict(
        db.query(EvidenceRecord.agent_session_id, func.count(EvidenceRecord.id))
        .filter(EvidenceRecord.agent_session_id.in_(ids))
        .group_by(EvidenceRecord.agent_session_id)
        .all()
    )
    for row in rows:
        row.host_test_count = tests.get(row.id, 0)
        row.evidence_count = evidence.get(row.id, 0)


def list_agent_sessions(
    db: Session,
    project_id: int,
    *,
    agent_id: Optional[int] = None,
    model: Optional[str] = None,
    tool: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    session_id: Optional[int] = None,
) -> List[AgentSessionRow]:
    """Return the agent-session list for a project.

    ``session_id`` narrows the list to that one session — the detail page's
    read (``get_agent_session_row``), so it is built by this path and cannot
    drift from the list row.

    Filters are AND'd.  Ordering is started_at DESC (most recent first, nulls
    last) with ``id`` as the deterministic tiebreaker.

    ``status`` matches the stored status column ('active' / 'ended'; a row
    from before v2.337.0 may also read 'expired').  Pass 'active' for the
    sessions still open.

    One query, ordered and paged in SQL, then one grouped query per derived
    value for the page's ids — never per row, and the same for every row.
    For ``total`` the endpoint calls ``count_agent_sessions()``.
    """
    q = db.query(AgentSession).filter(AgentSession.project_id == project_id)
    q = _apply_session_filters(
        q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status,
    )
    if session_id is not None:
        q = q.filter(AgentSession.id == session_id)
    sessions = (
        q.order_by(
            AgentSession.started_at.desc().nulls_last(),
            AgentSession.id,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    page = [
        AgentSessionRow(
            resume_supported=s.workflow == AgentSessionWorkflow.PROJECT.value,
            id=s.id,
            project_id=s.project_id,
            agent_id=s.agent_id,
            user_id=s.started_by_id,
            status=_status_value(s.status),
            started_at=s.started_at,
            completed_at=s.completed_at,
            generated_by_model=s.generated_by_model,
            generated_by_tool=s.generated_by_tool,
            prompt_version=s.prompt_version,
            agent_name=s.agent.name if s.agent else None,
            user_username=s.started_by.username if s.started_by else None,
            user_full_name=(s.started_by.full_name or None) if s.started_by else None,
            purpose=s.purpose,
            renewable_until=session_renewal_deadline(s),
            end_reason=s.end_reason,
            last_activity_at=s.last_activity_at,
        )
        for s in sessions
    ]
    ids = [r.id for r in page]
    # v2.340.0 — the live key's expiry, so the UI can tell "active, agent
    # alive" from "active, key lapsed, resumable until <deadline>".
    expiry = key_expiry_for_agent_sessions(db, ids)
    # v2.343.0 — feedback submissions: which sessions said anything on the way out.
    feedback = feedback_counts_for_agent_sessions(db, ids)
    # v2.432.0 — the operator's authority (the role the key is checked against).
    roles = _operator_roles(db, project_id, {r.user_id for r in page})
    # v2.449.0 — what the audit log and the notes say the session did.
    activity = activity_for_agent_sessions(db, ids)
    notes = note_counts_for_agent_sessions(db, ids)
    for r in page:
        r.key_expires_at = _aware_utc(expiry.get(r.id))
        r.feedback_count = feedback.get(r.id, 0)
        r.operator_role = roles.get(r.user_id)
        seen = activity.get(r.id) or SessionActivity()
        r.call_count = seen.call_count
        r.connection = seen.connection
        r.first_call_at = seen.first_call_at
        r.note_count = notes.get(r.id, 0)
    _attach_work_counts(db, page)
    return page


def summarise_by_model_tool(
    db: Session,
    project_id: int,
) -> List[dict]:
    """Sessions per ``(generated_by_model, generated_by_tool)`` — the Agent
    Sessions page's "By model and tool" table.

    One dict per pair: ``{generated_by_model, generated_by_tool, total}``,
    where ``total`` is the ``total`` of the list filtered by that model and
    tool (the same rows: ``_apply_session_filters``).  Sessions that reported
    neither are the ``(None, None)`` bucket, so they stay visible.  One
    ``GROUP BY`` (v2.43.3, AUD-O1 — never a count over a fetched page).
    """
    grouped = _apply_session_filters(
        db.query(
            AgentSession.generated_by_model,
            AgentSession.generated_by_tool,
            func.count(AgentSession.id),
        ).filter(AgentSession.project_id == project_id),
        agent_id=None, model=None, tool=None, user_id=None, status=None,
    ).group_by(AgentSession.generated_by_model, AgentSession.generated_by_tool).all()
    # Pairs with reported attribution first, then by how many.
    return sorted(
        (
            {"generated_by_model": model, "generated_by_tool": tool, "total": int(n)}
            for model, tool, n in grouped
        ),
        key=lambda b: (
            b["generated_by_model"] is None,
            b["generated_by_tool"] is None,
            -b["total"],
        ),
    )
