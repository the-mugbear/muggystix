"""
Agent sessions — lifecycle helpers and the unified timeline read path.

v2.337.0 — one project-scoped session per operator replaces the four
per-workflow entry points.  This module owns:

* **Lifecycle**: creating a session, minting / re-minting its key, ending a
  session, and lapsing sessions whose keys have all expired.  (A session had
  "phases" — the recon and execution runs it opened — until v2.433.0 and
  v2.442.0; it now simply proposes host tests and records evidence.)
* **The timeline**: the unified list the Agent Sessions page and Operations
  read — consolidated ``project`` sessions plus legacy assist sessions, which
  still surface from their detail table so history keeps its ids.

A SQL view would also work for the timeline but adds a schema artifact that
has to move with column changes.  A Python UNION is cheaper to evolve at the
current scale (O(10s) sessions per project) and lets the service add
cross-kind logic without DDL.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Literal, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models_agent import (
    Agent,
    AgentFeedback,
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
)
from app.db.models_auth import APIKey, User
from app.db.models_project import ProjectMembership
from app.services.agent_prompt_history import PROMPT_VERSION
from app.services.agent_key_ttl import resolve_expires_at, session_renewal_deadline
from app.services.assist_session_service import operator_role

logger = logging.getLogger(__name__)


# v2.337.0 — ``project`` is the consolidated kind every new session gets; the
# legacy ``assist`` kind remains so sessions started before the consolidation
# keep showing up (sourced from its detail table, keyed by those ids).  The
# legacy plan-generation and execution kinds went with their tables (v2.442.0).
SessionKind = Literal["project", "assist"]

#: The default kind set. Named once so the call sites can't drift — assist
#: was once missing from three of five copies of this list.
ALL_SESSION_KINDS = {"project", "assist"}

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
    """
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
            expires_at=resolve_expires_at(ttl_hours),
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


def _assist_ids_for_agent_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{agent_session_id: assist_sessions.id}`` — each consolidated session's
    detail row (notes and the API-call feed are keyed by it), one query."""
    if not session_ids:
        return {}
    return dict(
        db.query(AssistSession.agent_session_id, func.min(AssistSession.id))
        .filter(AssistSession.agent_session_id.in_(session_ids))
        .group_by(AssistSession.agent_session_id)
        .all()
    )


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
    """One consolidated session as the list shows it (work counts, key state,
    feedback, operator role), or None when it is not this project's."""
    rows = list_agent_sessions(db, project_id, kinds=["project"], session_id=session_id, limit=1)
    return rows[0] if rows else None


def key_expiry_for_agent_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{agent_session_id: max(expires_at)}`` over active keys, one query."""
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
    db.query(AssistSession).filter(
        AssistSession.agent_session_id == session.id,
        AssistSession.status == "active",
    ).update(
        {"status": "ended", "ended_at": session.completed_at},
        synchronize_session=False,
    )


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
        # The truthful timestamp is when access actually stopped.
        session.completed_at = expires_at or session.completed_at
        lapsed.append(session)
    # Assist detail rows mirror the status so their review page agrees, dated
    # to when access actually stopped (the session's completed_at — the key's
    # expiry), not to when the sweep happened to run.
    if lapsed:
        for s in lapsed:
            db.query(AssistSession).filter(
                AssistSession.agent_session_id == s.id,
                AssistSession.status == "active",
            ).update(
                {"status": "ended", "ended_at": s.completed_at or now},
                synchronize_session=False,
            )
        db.commit()
        logger.info(
            "Lapsed %d agent session(s) whose keys had expired: %s",
            len(lapsed), ", ".join(f"#{s.id}" for s in lapsed[:20]),
        )
    return len(lapsed)


@dataclass
class AgentSessionRow:
    """One row in the unified agent-session timeline.

    ``status`` is each session's native status field (no normalisation — the
    UI maps it to a presentation palette).
    """
    kind: SessionKind
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
    # v2.337.0 — the operator's stated purpose (consolidated sessions only).
    purpose: Optional[str] = None
    # v2.340.0 — when the session's live key stops working and until when the
    # session can still be renewed / resumed (project sessions only).  An
    # ``active`` row whose key has expired but is still renewable is a session
    # the operator can reconnect to, and the page needs both dates to say so.
    key_expires_at: Optional[datetime] = None
    renewable_until: Optional[datetime] = None
    # v2.343.0 — how the session ended (END_REASON_*; None while active) and
    # how many feedback submissions it made.  Project sessions only.
    end_reason: Optional[str] = None
    feedback_count: int = 0
    # v2.402.0 — the operator's display name (users.full_name), shown in
    # preference to the username; None when the account has none.
    user_full_name: Optional[str] = None
    # v2.402.0 — the agent session a legacy assist row belongs to, and whether
    # that session can still act.  The row keeps its own
    # status ("active") after the session whose key drove it has ended or its
    # key has run out — the run outlives the session.  ``session_live`` says
    # which: True = the session is active and holds a live or renewable key;
    # False = it has ended or can no longer be renewed, so nothing will move
    # this run on its own; None = not computed (project rows, or a run that is
    # not in progress).  Workflow state, not evidence freshness.
    agent_session_id: Optional[int] = None
    session_live: Optional[bool] = None
    # v2.432.0 — project sessions only.  ``assist_session_id`` is the
    # session's detail row, whose id the notes and the API-call feed are keyed
    # by; ``last_activity_at`` is its most recent authenticated call;
    # ``operator_role`` the authority it acts with
    # (``assist_session_service.operator_role``).
    # v2.442.0 — ``host_test_count`` / ``evidence_count``: the host tests the
    # session proposed and the evidence records it wrote (the work a session
    # leaves behind; they replace ``phases``, the runs it used to open).
    host_test_count: int = 0
    evidence_count: int = 0
    assist_session_id: Optional[int] = None
    last_activity_at: Optional[datetime] = None
    operator_role: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
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
            "agent_session_id": self.agent_session_id,
            "session_live": self.session_live,
            "host_test_count": self.host_test_count,
            "evidence_count": self.evidence_count,
            "assist_session_id": self.assist_session_id,
            "last_activity_at": self.last_activity_at,
            "operator_role": self.operator_role,
        }


# Run statuses that mean "still in progress" — the only ones for which a
# lapsed session is worth saying (a completed run needs no session).
_RUN_IN_PROGRESS = {"active", "in_progress"}


def _attach_session_liveness(db: Session, rows: "List[AgentSessionRow]") -> None:
    """Set ``session_live`` on in-progress run rows (v2.402.0).

    Two grouped queries whatever the page size: the parent sessions (status +
    renewal deadline) and their live keys.  A legacy run with no parent
    session falls back to its agent's keys — that is what authenticated it.
    """
    runs = [
        r for r in rows
        if r.kind == "assist" and (r.status or "").lower() in _RUN_IN_PROGRESS
    ]
    if not runs:
        return
    liveness = runs_session_live(db, [(r.agent_session_id, r.agent_id) for r in runs])
    for r, live in zip(runs, liveness):
        r.session_live = live


def runs_session_live(
    db: Session, runs: "List[Tuple[Optional[int], Optional[int]]]",
) -> "List[Optional[bool]]":
    """For each in-progress run's ``(agent_session_id, agent_id)``: can
    something still act on it?  True / False, or None when neither is known.

    The ONE rule (v2.424.0 — Operations' Blocked strip had its own, which
    missed a legacy run with no parent session and a session whose key had
    run out, so the Runs list said "stalled" while Blocked listed nothing).
    A run with a parent session is live while that session is active and its
    key, or its renewal window, has not run out; a legacy run with no parent
    falls back to its agent's keys — that is what authenticated it.  Two
    grouped queries whatever the count."""
    now = datetime.now(timezone.utc)

    def _aware(t):
        return t if t is None or t.tzinfo is not None else t.replace(tzinfo=timezone.utc)

    session_ids = sorted({sid for sid, _ in runs if sid is not None})
    live_sessions: set = set()
    if session_ids:
        expiry = key_expiry_for_agent_sessions(db, session_ids)
        for s in db.query(AgentSession).filter(AgentSession.id.in_(session_ids)).all():
            if s.status != SESSION_ACTIVE:
                continue
            key_exp = _aware(expiry.get(s.id))
            renew = session_renewal_deadline(s)
            if (key_exp is not None and key_exp > now) or (renew is not None and renew > now):
                live_sessions.add(s.id)

    orphan_agents = sorted({aid for sid, aid in runs if sid is None and aid is not None})
    live_agents: set = set()
    if orphan_agents:
        live_agents = {
            agent_id
            for (agent_id,) in (
                db.query(APIKey.agent_id)
                .filter(
                    APIKey.agent_id.in_(orphan_agents),
                    APIKey.is_active.is_(True),
                    APIKey.expires_at.is_(None) | (APIKey.expires_at > now),
                )
                .distinct()
                .all()
            )
        }

    out: "List[Optional[bool]]" = []
    for sid, aid in runs:
        if sid is not None:
            out.append(sid in live_sessions)
        elif aid is not None:
            out.append(aid in live_agents)
        else:
            out.append(None)
    return out


def _not_a_project_child(detail_agent_session_col):
    """True for a legacy detail row: it has no parent project AgentSession, so
    it earns its own timeline row. New (project-session) detail rows are
    represented by their session's row and are excluded here (v2.337.0)."""
    from sqlalchemy import exists, and_
    return ~exists().where(
        and_(
            AgentSession.id == detail_agent_session_col,
            AgentSession.workflow == AgentSessionWorkflow.PROJECT.value,
        )
    )


def _apply_assist_filters(q, *, agent_id, model, tool, user_id, status):
    """Legacy assist rows come from ``AssistSession``, not from the unified
    ``AgentSession`` base row: the detail table is where that workflow's own
    lifecycle lives.
    """
    if agent_id is not None:
        q = q.filter(AssistSession.agent_id == agent_id)
    if model is not None:
        q = q.filter(AssistSession.generated_by_model == model)
    if tool is not None:
        q = q.filter(AssistSession.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(AssistSession.started_by_id == user_id)
    if status is not None:
        q = q.filter(AssistSession.status == status)
    return q.filter(_not_a_project_child(AssistSession.agent_session_id))



def _apply_project_filters(q, *, agent_id, model, tool, user_id, status):
    """Consolidated sessions come straight off ``agent_sessions``."""
    q = q.filter(AgentSession.workflow == AgentSessionWorkflow.PROJECT.value)
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
    kinds: Optional[List[SessionKind]] = None,
    agent_id: Optional[int] = None,
    model: Optional[str] = None,
    tool: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
) -> int:
    """v2.43.3 (AUD-O1): count matching sessions across the three kinds
    via three SELECT COUNT(*) queries.  Cheap (uses each table's
    project_id index) and — unlike the old approach of fetching up to
    10_000 rows and computing ``len()`` in Python — never silently
    under-reports the total on long-lived projects.
    """
    from sqlalchemy import func as _func

    total = 0
    want = set(kinds) if kinds is not None else set(ALL_SESSION_KINDS)

    if "project" in want:
        q = db.query(_func.count(AgentSession.id)).filter(
            AgentSession.project_id == project_id
        )
        q = _apply_project_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        total += q.scalar() or 0


    if "assist" in want:
        q = db.query(_func.count(AssistSession.id)).filter(
            AssistSession.project_id == project_id
        )
        q = _apply_assist_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        total += q.scalar() or 0

    return total


def _status_value(status) -> str:
    """A status column as its string (some are enum columns, some strings)."""
    return status.value if hasattr(status, "value") else str(status)


def _aware_utc(t: Optional[datetime]) -> Optional[datetime]:
    """Read a tz-naive timestamp as UTC so mixed columns sort together."""
    return t if t is None or t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _attach_work_counts(db: Session, rows: "List[AgentSessionRow]") -> None:
    """Set ``host_test_count`` / ``evidence_count`` on project rows (v2.442.0):
    two grouped queries whatever the page size."""
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    ids = [r.id for r in rows if r.kind == "project"]
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
        if row.kind == "project":
            row.host_test_count = tests.get(row.id, 0)
            row.evidence_count = evidence.get(row.id, 0)


def list_agent_sessions(
    db: Session,
    project_id: int,
    *,
    kinds: Optional[List[SessionKind]] = None,
    agent_id: Optional[int] = None,
    model: Optional[str] = None,
    tool: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    session_id: Optional[int] = None,
) -> List[AgentSessionRow]:
    """Return the unified agent-session list for a project.

    ``session_id`` narrows the project kind to that one session — the detail
    page's read (``get_agent_session_row``), so it is built by this path and
    cannot drift from the list row.

    Filters are AND'd.  Ordering is started_at DESC (most recent first)
    with ``id`` + ``kind`` as deterministic tiebreakers.

    ``status`` matches each kind's native status column ('active' / 'ended'
    for a project session).  Pass 'active' for the in-flight banner.

    The query strategy is one query per kind, each pre-filtered +
    pre-sorted, merged + sorted + sliced in Python.

    v2.43.3 (AUD-O1): each per-kind SQL query is bounded by
    ``offset + limit`` (instead of fetching every matching row), so a
    project with thousands of sessions doesn't materialize all of them
    in Python.  For accurate ``total``, the
    endpoint calls ``count_agent_sessions()`` separately.
    """
    rows: List[AgentSessionRow] = []
    want = set(kinds) if kinds is not None else set(ALL_SESSION_KINDS)
    # Each per-kind query needs at least (offset + limit) rows so that
    # after merge-sort we can correctly slice the requested page; the
    # discarded slop is small relative to fetching the whole table.
    per_kind_cap = max(offset + limit, 1)

    if "project" in want:
        q = db.query(AgentSession).filter(AgentSession.project_id == project_id)
        q = _apply_project_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        if session_id is not None:
            q = q.filter(AgentSession.id == session_id)
        for s in q.order_by(AgentSession.started_at.desc()).limit(per_kind_cap).all():
            rows.append(AgentSessionRow(
                kind="project",
                id=s.id,
                project_id=s.project_id,
                agent_id=s.agent_id,
                user_id=s.started_by_id,
                status=s.status,
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
            ))
        # v2.340.0 — one grouped query for the live key's expiry on every
        # project row on the page, so the UI can tell "active, agent alive"
        # from "active, key lapsed, resumable until <deadline>".
        project_ids = [r.id for r in rows if r.kind == "project"]
        expiry = key_expiry_for_agent_sessions(db, project_ids)
        # v2.343.0 — and one for feedback submissions, so the page can show
        # which sessions said anything on the way out.
        feedback = feedback_counts_for_agent_sessions(db, project_ids)
        # v2.432.0 — and one each for the detail row's id and the operator's
        # authority (the role the key is checked against on every call).
        assist_ids = _assist_ids_for_agent_sessions(db, project_ids)
        roles = _operator_roles(db, project_id, {r.user_id for r in rows if r.kind == "project"})
        for r in rows:
            if r.kind == "project":
                exp = expiry.get(r.id)
                if exp is not None and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                r.key_expires_at = exp
                r.feedback_count = feedback.get(r.id, 0)
                r.assist_session_id = assist_ids.get(r.id)
                r.operator_role = roles.get(r.user_id)


    if "assist" in want:
        # v2.303.0 — legacy assist sessions (project-scoped).
        q = db.query(AssistSession).filter(AssistSession.project_id == project_id)
        q = _apply_assist_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for a in q.order_by(AssistSession.started_at.desc()).limit(per_kind_cap).all():
            rows.append(AgentSessionRow(
                kind="assist",
                id=a.id,
                project_id=a.project_id,
                agent_id=a.agent_id,
                user_id=a.started_by_id,
                # AssistSessionStatus is an enum column; the other three kinds
                # store plain strings, and the row shape is a string.
                status=a.status.value if hasattr(a.status, "value") else str(a.status),
                started_at=a.started_at,
                # Assist calls its completion `ended_at`; the timeline calls it
                # completed_at. Same event.
                completed_at=a.ended_at,
                generated_by_model=a.generated_by_model,
                generated_by_tool=a.generated_by_tool,
                prompt_version=a.prompt_version,
                agent_name=a.agent.name if a.agent else None,
                user_username=a.started_by.username if a.started_by else None,
                user_full_name=(a.started_by.full_name or None) if a.started_by else None,
                agent_session_id=a.agent_session_id,
            ))


    # Stable ordering: most-recent started_at first; nulls last;
    # then by (kind, id) so two rows with identical timestamps
    # don't flip-flop between calls.
    rows.sort(
        key=lambda r: (
            r.started_at is None,  # nulls last
            -(r.started_at.timestamp() if r.started_at else 0),
            r.kind,
            r.id,
        )
    )
    page = rows[offset : offset + limit]
    _attach_work_counts(db, page)
    _attach_session_liveness(db, page)
    return page


def summarise_by_model_tool(
    db: Session,
    project_id: int,
) -> List[dict]:
    """Aggregate sessions by ``(generated_by_model, generated_by_tool)``
    for the v3 per-model rollup card.

    Returns one dict per tuple with counts of each kind so the UI
    can render "claude-opus-4-7 / claude-code: 5 sessions".  Rows with null model+tool are folded into the
    ``(None, None)`` bucket so they're visible — usually the
    pre-v2.28 / pre-v2.30 sessions that never reported attribution.

    v2.43.3 (AUD-O1): aggregation pushed to SQL ``GROUP BY``.  The
    pre-fix path fetched up to 10_000 rows via list_agent_sessions and
    counted in Python, which silently truncated long-lived projects'
    rollups.  Four small GROUP BY queries with the same project_id
    filter are cheap (each table has the index) and complete.
    """
    from sqlalchemy import func as _func

    counts: dict[tuple, dict] = {}

    def _bucket(model, tool, kind, n):
        key = (model, tool)
        bucket = counts.setdefault(key, {
            "generated_by_model": model,
            "generated_by_tool": tool,
            "project": 0,
            "assist": 0,
            "total": 0,
        })
        bucket[kind] += int(n)
        bucket["total"] += int(n)

    project_rows = (
        db.query(
            AgentSession.generated_by_model,
            AgentSession.generated_by_tool,
            _func.count(AgentSession.id),
        )
        .filter(
            AgentSession.project_id == project_id,
            AgentSession.workflow == AgentSessionWorkflow.PROJECT.value,
        )
        .group_by(AgentSession.generated_by_model, AgentSession.generated_by_tool)
        .all()
    )
    for model, tool, n in project_rows:
        _bucket(model, tool, "project", n)

    # v2.303.0 — assist counts here too, or the rollup card silently
    # under-reports what a given model/tool has been doing on the project.
    assist_rows = (
        db.query(
            AssistSession.generated_by_model,
            AssistSession.generated_by_tool,
            _func.count(AssistSession.id),
        )
        .filter(AssistSession.project_id == project_id)
        .group_by(AssistSession.generated_by_model, AssistSession.generated_by_tool)
        .all()
    )
    for model, tool, n in assist_rows:
        _bucket(model, tool, "assist", n)

    # Sort: tuples with reported attribution first, total DESC.
    out = sorted(
        counts.values(),
        key=lambda b: (
            b["generated_by_model"] is None,
            b["generated_by_tool"] is None,
            -b["total"],
        ),
    )
    return out
