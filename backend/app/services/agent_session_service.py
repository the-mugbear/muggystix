"""
Agent sessions — lifecycle helpers and the unified timeline read path.

v2.337.0 — one project-scoped session per operator replaces the four
per-workflow entry points.  This module owns:

* **Lifecycle**: creating a session, minting / re-minting its key, resolving
  the phase a call is about (the recon run / execution run a session has
  open), ending a session, and lapsing sessions whose keys have all expired.
* **The timeline**: the unified list the Agent Sessions page and Operations
  read — consolidated ``project`` sessions plus the four legacy kinds, which
  still surface from their detail tables so history keeps its ids.

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
    ExecutionSession,
    ExecutionSessionStatus,
    TestPlan,
    TestPlanHistory,
)
from app.db.models_auth import APIKey, User
from app.db.models_project import ProjectMembership
from app.services.agent_key_ttl import resolve_expires_at, session_renewal_deadline
from app.services.assist_session_service import operator_role

logger = logging.getLogger(__name__)


# v2.337.0 — ``project`` is the consolidated kind every new session gets; the
# four legacy kinds remain so sessions started before the consolidation keep
# showing up (they are sourced from their detail tables, keyed by those ids).
SessionKind = Literal["project", "plan_generation", "execution", "assist"]

#: The default kind set. Named once so the call sites can't drift — assist
#: was once missing from three of five copies of this list.
ALL_SESSION_KINDS = {"project", "plan_generation", "execution", "assist"}

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
    no single target — the recon/execution phase rows do
(``ExecutionSession.test_plan_id``).
    """
    base = AgentSession(
        workflow=workflow,
        project_id=project_id,
        agent_id=agent_id,
        started_by_id=started_by_id,
        purpose=(purpose or "").strip() or None,
        status=status,
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


def active_execution_phases(db: Session, session_id: int) -> List[ExecutionSession]:
    return (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.agent_session_id == session_id,
            ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        )
        .order_by(ExecutionSession.id)
        .all()
    )


def resolve_execution_phase(
    db: Session,
    session_id: int,
    execution_session_id: Optional[int] = None,
    *,
    plan_id: Optional[int] = None,
) -> ExecutionSession:
    """The execution run a call is about — by id, by plan, or the single
    active one."""
    q = db.query(ExecutionSession).filter(
        ExecutionSession.agent_session_id == session_id
    )
    if execution_session_id is not None:
        row = q.filter(ExecutionSession.id == execution_session_id).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Execution run #{execution_session_id} was not opened by "
                    "this session. Open one with POST /agent/execution-sessions/start."
                ),
            )
        return row
    if plan_id is not None:
        row = (
            q.filter(
                ExecutionSession.test_plan_id == plan_id,
                ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
            )
            .order_by(ExecutionSession.id.desc())
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "no_active_execution_run",
                    "message": (
                        f"This session has no active execution run on plan #{plan_id}. "
                        "Open one with POST /agent/execution-sessions/start "
                        "{\"plan_id\": " + str(plan_id) + "}."
                    ),
                },
            )
        return row
    active = active_execution_phases(db, session_id)
    if len(active) == 1:
        return active[0]
    if not active:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_active_execution_run",
                "message": (
                    "This session has no active execution run. Open one with "
                    "POST /agent/execution-sessions/start {\"plan_id\": ...}."
                ),
            },
        )
    raise HTTPException(
        status_code=400,
        detail={
            "error": "ambiguous_execution_run",
            "message": "This session has more than one active execution run; say which.",
            "execution_session_ids": [e.id for e in active],
        },
    )


def open_execution_phase(
    db: Session,
    *,
    session: AgentSession,
    plan: TestPlan,
) -> ExecutionSession:
    """Open (or resume) an execution run on ``plan`` within ``session``.

    The plan must be a draft or already in progress, and non-empty.  There is
    no approval step (v2.433.0): the operator drives their agent, and a plan
    is the record of what it set out to test.  The first run moves a draft to
    in_progress.  At most one run per plan is active at a time: a run this session
    already has open on the plan is reused; any other session's active run
    is paused, as ``/execute`` always did.  Raises HTTPException.
    """
    if plan.project_id != session.project_id:
        raise HTTPException(status_code=404, detail="Test plan not found")
    # Serialise concurrent opens on the plan row (Postgres FOR UPDATE; a no-op
    # on SQLite, where the partial-unique index is the backstop).  ``refresh``
    # rather than a bare locking query: the caller loaded ``plan`` before the
    # lock, and a query that returns an already-loaded object leaves its
    # attributes untouched, so the status checked below would be the
    # pre-lock value and an archive that committed while we waited would be
    # invisible.  Refreshing under the lock reads the state the lock protects.
    db.refresh(plan, with_for_update=True)
    if plan.status not in ("draft", "in_progress"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan #{plan.id} is {plan.status}; only a draft or in-progress "
                "plan can be executed."
            ),
        )
    from app.db.models_agent import TestPlanEntry
    entry_count = (
        db.query(func.count(TestPlanEntry.id))
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .scalar()
    ) or 0
    if entry_count == 0:
        raise HTTPException(status_code=409, detail="Cannot execute an empty test plan.")
    # A draft still being written belongs to the session writing it
    # (v2.433.1).  Starting a run freezes its tests, so another session may
    # start it only once its drafting session has ended.  Ownership, not
    # approval: the drafting session itself starts it whenever it likes.
    if (
        plan.status == "draft"
        and plan.agent_session_id is not None
        and plan.agent_session_id != session.id
    ):
        drafter_active = (
            db.query(AgentSession.id)
            .filter(AgentSession.id == plan.agent_session_id, AgentSession.status == SESSION_ACTIVE)
            .first()
        )
        if drafter_active is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Plan #{plan.id} is a draft still open in agent session "
                    f"#{plan.agent_session_id}, and starting a run freezes its tests. "
                    "Start it from that session, or once that session has ended."
                ),
            )

    own = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.agent_session_id == session.id,
            ExecutionSession.test_plan_id == plan.id,
            ExecutionSession.status.in_([
                ExecutionSessionStatus.ACTIVE.value,
                ExecutionSessionStatus.PAUSED.value,
            ]),
        )
        .order_by(ExecutionSession.id.desc())
        .first()
    )
    db.query(ExecutionSession).filter(
        ExecutionSession.test_plan_id == plan.id,
        ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        ExecutionSession.id != (own.id if own is not None else -1),
    ).update({"status": ExecutionSessionStatus.PAUSED.value}, synchronize_session=False)
    db.flush()
    if own is not None:
        own.status = ExecutionSessionStatus.ACTIVE.value
        run = own
    else:
        run = ExecutionSession(
            test_plan_id=plan.id,
            agent_id=session.agent_id,
            started_by_id=session.started_by_id,
            status=ExecutionSessionStatus.ACTIVE.value,
            agent_session_id=session.id,
        )
        _copy_probe(session, run)
        db.add(run)
    if plan.status == "draft":
        plan.status = "in_progress"
        # The only lifecycle step between drafting and completion now that
        # there is no approval, so the plan's history records it.
        actor = (
            ("agent", session.agent_id) if session.agent_id is not None
            else ("user", session.started_by_id)
        )
        if actor[1] is not None:
            db.add(TestPlanHistory(
                test_plan_id=plan.id,
                entry_id=None,
                actor_type=actor[0],
                actor_id=actor[1],
                action="status_changed",
                field_changed="status",
                old_value="draft",
                new_value="in_progress",
            ))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Another execution run became active on this plan concurrently. "
                "Retry."
            ),
        ) from exc
    return run


def _copy_probe(session: AgentSession, run) -> None:
    """Snapshot the session's environment probe + attribution onto a phase row."""
    if session.environment_probed_at is None:
        return
    run.environment = session.environment
    run.environment_probed_at = session.environment_probed_at
    run.environment_probed_by_user_id = session.environment_probed_by_user_id
    run.environment_probed_from_ip = session.environment_probed_from_ip
    run.generated_by_model = session.generated_by_model
    run.generated_by_tool = session.generated_by_tool
    run.prompt_version = session.prompt_version


def propagate_probe(db: Session, session: AgentSession) -> None:
    """After a (re-)probe on the session, refresh every open phase's copy."""
    for run in db.query(ExecutionSession).filter(
        ExecutionSession.agent_session_id == session.id,
        ExecutionSession.status.in_([
            ExecutionSessionStatus.ACTIVE.value, ExecutionSessionStatus.PAUSED.value,
        ]),
    ).all():
        _copy_probe(session, run)


def session_phase_summary(db: Session, session: AgentSession) -> dict:
    """The phases a session has open or produced — what ``/agent/identity``
    reports and what the MCP layer fills tool arguments from."""
    execs = active_execution_phases(db, session.id)
    drafted = [
        pid for (pid,) in db.query(TestPlan.id)
        .filter(TestPlan.agent_session_id == session.id)
        .order_by(TestPlan.id)
        .all()
    ]
    # The plan a bare ``plan_id`` means: the active execution's plan, else the
    # newest plan this session drafted.
    plan_id: Optional[int] = None
    if len(execs) == 1:
        plan_id = execs[0].test_plan_id
    elif drafted:
        plan_id = drafted[-1]
    return {
        "execution_session_id": execs[0].id if len(execs) == 1 else None,
        "active_execution_session_ids": [e.id for e in execs],
        "plan_id": plan_id,
        "drafted_plan_ids": drafted,
    }


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
    """End a session: revoke its keys and close what it left open.

    Its open execution runs (active, or paused because another session took
    the plan over) are abandoned, with their results kept: only the session
    that opened a run can continue it, so once the session is gone the run
    can never move again.  Pausing them (before v2.433.1) left them "left
    open" and "blocked" for good.  A later session opens a fresh run on the
    same plan and continues from the recorded results.  Draft plans stay
    drafts — they are project data, not session state.

    ``end_reason`` is the typed classification (``END_REASON_*``); when it is
    not given, a caller that names a person is an operator end and anything
    else is the sweep, which keeps the two pre-existing callers correct.
    """
    now = datetime.now(timezone.utc)
    who = (ended_by.full_name or ended_by.username) if ended_by is not None else "system"
    line = f"[{now.isoformat()}] Session ended by {who}" + (f": {reason}" if reason else "")

    revoke_session_keys(db, session.id)
    open_runs = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.agent_session_id == session.id,
            ExecutionSession.status.in_((
                ExecutionSessionStatus.ACTIVE.value, ExecutionSessionStatus.PAUSED.value,
            )),
        )
        .all()
    )
    for run in open_runs:
        run.status = ExecutionSessionStatus.ABANDONED.value
        run.completed_at = now
        run.notes = (f"{run.notes}\n{line}" if run.notes else line)[-8192:]
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
    """One consolidated session as the list shows it (phases, key state,
    feedback, operator role), or None when it is not this project's."""
    rows = list_agent_sessions(db, project_id, kinds=["project"], session_id=session_id, limit=1)
    return rows[0] if rows else None


def feedback_checkpoint(db: Session, agent_session_id: Optional[int]) -> Tuple[Optional[bool], Optional[str]]:
    """The nudge a phase-completion response carries (v2.343.0).

    Returns ``(feedback_recorded, hint)``.  Phase completion is reached far
    more reliably than the session end the feedback ask used to hang off, so
    it is the checkpoint: when the session has filed nothing yet, the hint says
    so and says what to do.  Advisory only — a completion is never refused
    over it, because refusing would make the exit rarer, not the feedback more
    common.  ``(None, None)`` when the call has no session to attribute to.
    """
    if agent_session_id is None:
        return None, None
    recorded = bool(feedback_counts_for_agent_sessions(db, [agent_session_id]).get(agent_session_id))
    if recorded:
        return True, None
    return False, (
        "This session has not filed any feedback yet. Before you end the "
        "session, POST /agent/feedback (MCP submit_feedback) with the friction "
        "you hit in this phase — each endpoint or tool where you retried, "
        "guessed, or worked around something, with the exact error or missing "
        "field. One line each is enough; 'the API was fine' is not."
    )


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
    session timed out while a scan ran) — the session, its open phases and its
    key are all still good, only the process holding the key is gone.  The
    operator either still has the key configured (then nothing needs minting;
    they reopen the client) or has lost it, in which case this mints a
    replacement on the **same** session: ``mint_session_key`` revokes the
    previous key first, so the dead client cannot keep writing beside the new
    one, and the open recon / execution phases stay attached because their
    ``agent_session_id`` never changes.  It is the only resume (v2.433.0).

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
    lifetime cap, a week by default — could end a project session.  Recon and
    execution runs had a ``/complete``; the session itself had nothing, so
    even an agent that finished cleanly left an active row behind.

    Refuses (409) while an execution run is still open, naming the ids: the
    run's own ``/complete`` records a truthful outcome, and ending underneath
    it would pause the run — the crashed-agent semantics, which this is not.
    """
    open_exec = [e.id for e in active_execution_phases(db, session.id)]
    if open_exec:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Close your open execution runs first — POST "
                    "/agent/execution-sessions/{id}/complete for each — then end "
                    "the session."
                ),
                "active_execution_session_ids": open_exec,
            },
        )
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

    The three workflows have different native shapes; this is the
    least-common-denominator the v3 UI consumes.  ``test_plan_id`` is
    populated for plan_generation + execution.  ``status`` is each session's
    native status field (no normalisation — the v3 UI can map them
    to a presentation palette).
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
    test_plan_id: Optional[int]
    # Denormalised display labels — populated by joining agents/users
    # at the service layer so the UI doesn't have to round-trip.
    agent_name: Optional[str] = None
    user_username: Optional[str] = None
    # v2.306.0 — what this session declared it is working on, in words.
    #
    # The ids above have always been here, but "Scope #3" tells a second
    # analyst nothing, and the whole reason a session declares a target is so
    # somebody else can see the range is taken before duplicating hours of
    # scanning. Recon resolves to the scope name plus its CIDRs; plan work
    # resolves to the plan title; assist is project-wide and has none.
    target_label: Optional[str] = None
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
    # v2.402.0 — the agent session a run row belongs to, and whether that
    # session can still act.  A recon / execution / assist row keeps its own
    # status ("active") after the session whose key drove it has ended or its
    # key has run out — the run outlives the session.  ``session_live`` says
    # which: True = the session is active and holds a live or renewable key;
    # False = it has ended or can no longer be renewed, so nothing will move
    # this run on its own; None = not computed (project rows, or a run that is
    # not in progress).  Workflow state, not evidence freshness.
    agent_session_id: Optional[int] = None
    session_live: Optional[bool] = None
    # v2.432.0 — project sessions only.  ``phases`` is the work the session
    # opened (recon runs, plans drafted, execution runs), each with its own
    # status: the timeline excludes those rows (``_not_a_project_child``), so
    # without this a session's runs were reachable only from /executions and
    # /recon/runs.  ``assist_session_id`` is the session's detail row, whose id
    # the notes and the API-call feed are keyed by; ``last_activity_at`` is its
    # most recent authenticated call; ``operator_role`` the authority it acts
    # with (``assist_session_service.operator_role``).
    phases: Optional[List[dict]] = None
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
            "test_plan_id": self.test_plan_id,
            "agent_name": self.agent_name,
            "user_username": self.user_username,
            "target_label": self.target_label,
            "purpose": self.purpose,
            "key_expires_at": self.key_expires_at,
            "renewable_until": self.renewable_until,
            "end_reason": self.end_reason,
            "feedback_count": self.feedback_count,
            "user_full_name": self.user_full_name,
            "agent_session_id": self.agent_session_id,
            "session_live": self.session_live,
            "phases": self.phases or [],
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
        if r.kind in ("execution", "assist") and (r.status or "").lower() in _RUN_IN_PROGRESS
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
    it earns its own timeline row. New (project-session) phase rows are
    represented by their session's row and are excluded here (v2.337.0)."""
    from sqlalchemy import exists, and_
    return ~exists().where(
        and_(
            AgentSession.id == detail_agent_session_col,
            AgentSession.workflow == AgentSessionWorkflow.PROJECT.value,
        )
    )


def _apply_plan_filters(q, *, agent_id, model, tool, user_id, status):
    if agent_id is not None:
        q = q.filter(TestPlan.agent_id == agent_id)
    if model is not None:
        q = q.filter(TestPlan.generated_by_model == model)
    if tool is not None:
        q = q.filter(TestPlan.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(TestPlan.created_by_user_id == user_id)
    if status is not None:
        q = q.filter(TestPlan.status == status)
    return q.filter(_not_a_project_child(TestPlan.agent_session_id))


def _plan_generation_status(plan_status: str) -> str:
    """Collapse TestPlan.status to the legacy plan-generation row's lifecycle.

    A draft is still being written ("in_progress"); once execution starts or
    the plan completes, generation is done ("completed"); archived stays
    archived.  Unknown values pass through so a future status never silently
    reads as in progress.
    """
    if plan_status == "draft":
        return "in_progress"
    if plan_status in ("in_progress", "completed"):
        return "completed"
    return plan_status


def _apply_assist_filters(q, *, agent_id, model, tool, user_id, status):
    """Assist rows come from ``AssistSession``, not from the unified
    ``AgentSession`` base row, for the same reason the other three do: the
    detail table is where the workflow's own lifecycle lives.

    A full collapse onto ``AgentSession`` is a bigger change than it looks —
    plan_generation rows are keyed by ``TestPlan.id`` (so switching would
    change every id the UI links on) and their status is derived live from
    ``TestPlan.status``, which nothing copies onto the base row. Adding the
    missing kind is the part that was actually load-bearing.
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


def _apply_execution_filters(q, *, agent_id, model, tool, user_id, status):
    if agent_id is not None:
        q = q.filter(ExecutionSession.agent_id == agent_id)
    if model is not None:
        q = q.filter(ExecutionSession.generated_by_model == model)
    if tool is not None:
        q = q.filter(ExecutionSession.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(ExecutionSession.started_by_id == user_id)
    if status is not None:
        q = q.filter(ExecutionSession.status == status)
    return q.filter(_not_a_project_child(ExecutionSession.agent_session_id))


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

    if "plan_generation" in want:
        q = db.query(_func.count(TestPlan.id)).filter(TestPlan.project_id == project_id)
        q = _apply_plan_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        total += q.scalar() or 0

    if "execution" in want:
        q = (
            db.query(_func.count(ExecutionSession.id))
            .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
            .filter(TestPlan.project_id == project_id)
        )
        q = _apply_execution_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
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


def _attach_target_labels(db: Session, rows: "List[AgentSessionRow]") -> None:
    """Fill ``target_label`` for a page of rows in a fixed number of queries.

    v2.306.0.  Batched deliberately: this runs on the Agent Runs list, and a
    per-row lookup would put the timeline back into N+1 for a purely cosmetic
    field.  Legacy rows carry one target (a scope or a plan); a consolidated
    session declares its targets per phase, so its label is one line naming
    the scopes it scanned, the plans it drafted, the plans it executed.

    v2.432.0 — the same three queries also give each consolidated session its
    ``phases``: one entry per run or plan, in the order it was opened, with its
    own status and label, so the page can list and link a session's work.
    """
    project_ids = [r.id for r in rows if r.kind == "project"]

    # Per-phase targets of the consolidated sessions on this page.
    drafted_by: dict[int, List[str]] = {}
    executed_by: dict[int, List[str]] = {}
    # (sid, opened_at, phase)
    phases: List[tuple] = []
    if project_ids:
        for sid, pid, title, status, opened in (
            db.query(
                TestPlan.agent_session_id, TestPlan.id, TestPlan.title,
                TestPlan.status, TestPlan.created_at,
            )
            .filter(TestPlan.agent_session_id.in_(project_ids))
            .order_by(TestPlan.id)
            .all()
        ):
            drafted_by.setdefault(sid, []).append(title)
            phases.append((sid, opened, {
                "kind": "plan", "id": pid, "status": _status_value(status),
                "label": title, "test_plan_id": pid, "started_at": opened,
            }))
        for sid, eid, plan_id, title, status, opened in (
            db.query(
                ExecutionSession.agent_session_id, ExecutionSession.id,
                ExecutionSession.test_plan_id, TestPlan.title,
                ExecutionSession.status, ExecutionSession.started_at,
            )
            .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
            .filter(ExecutionSession.agent_session_id.in_(project_ids))
            .order_by(ExecutionSession.id)
            .all()
        ):
            executed_by.setdefault(sid, []).append(title)
            phases.append((sid, opened, {
                "kind": "execution", "id": eid, "status": _status_value(status),
                "label": title, "test_plan_id": plan_id, "started_at": opened,
            }))

    phases_by: dict[int, List[dict]] = {}
    for sid, opened, phase in sorted(
        phases, key=lambda p: (p[1] is None, _aware_utc(p[1]) or datetime.min.replace(tzinfo=timezone.utc)),
    ):
        phases_by.setdefault(sid, []).append(phase)

    plan_ids = {r.test_plan_id for r in rows if r.test_plan_id is not None}
    plan_labels: dict[int, str] = {}
    if plan_ids:
        plan_labels = dict(
            db.query(TestPlan.id, TestPlan.title)
            .filter(TestPlan.id.in_(plan_ids)).all()
        )

    for r in rows:
        if r.kind == "project":
            parts: List[str] = []
            if r.id in drafted_by:
                parts.append("drafted " + "; ".join(drafted_by[r.id]))
            if r.id in executed_by:
                parts.append("executed " + "; ".join(executed_by[r.id]))
            r.target_label = " · ".join(parts) or None
            r.phases = phases_by.get(r.id, [])
        elif r.test_plan_id is not None:
            r.target_label = plan_labels.get(r.test_plan_id) or None
        # assist: project-wide by design — no target, and saying so is the UI's
        # job, not a fake label here.


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

    ``status`` matches each kind's native status column (recon +
    execution use 'active' / 'paused' / 'completed' / 'failed' /
    'abandoned'; plan_generation uses the collapsed plan status —
    'in_progress' / 'completed' / 'archived').  Pass 'active' for the in-flight
    banner; pass 'completed' for a "last week's runs" view.

    The query strategy is three separate per-kind queries, each
    pre-filtered + pre-sorted, merged + sorted + sliced in Python.
    At project scale (O(10s)-O(100s) sessions per project) this is
    cheaper than a Postgres view UNION because each underlying query
    can use its own index.

    v2.43.3 (AUD-O1): each per-kind SQL query is now bounded by
    ``offset + limit`` (instead of fetching every matching row), so a
    project with thousands of sessions doesn't materialize all of them
    in Python.  Worst case the merge-sort runs across
    ``3 * (offset + limit)`` rows.  For accurate ``total``, the
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
                test_plan_id=None,
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

    if "plan_generation" in want:
        # Plan creation is the closest analogue to a "session" for the
        # plan-generation workflow.  Each TestPlan row is one
        # creation event; the timeline uses ``created_at`` as the
        # started_at.
        #
        # The displayed status is GENERATION-BOUNDED, not the plan's
        # full lifecycle (_plan_generation_status): execution has its
        # own run rows.
        q = db.query(TestPlan).filter(TestPlan.project_id == project_id)
        q = _apply_plan_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for p in q.order_by(TestPlan.created_at.desc()).limit(per_kind_cap).all():
            gen_status = _plan_generation_status(p.status)
            rows.append(AgentSessionRow(
                kind="plan_generation",
                id=p.id,
                project_id=p.project_id,
                agent_id=p.agent_id,
                user_id=p.created_by_user_id,
                status=gen_status,
                started_at=p.created_at,
                # Generation is "done" the moment the plan leaves
                # DRAFT.  updated_at is a reasonable proxy because
                # the /submit call is the last write the agent makes;
                # subsequent edits (entry additions etc. by humans)
                # don't shift this materially.
                completed_at=p.updated_at if p.status != "draft" else None,
                generated_by_model=p.generated_by_model,
                generated_by_tool=p.generated_by_tool,
                prompt_version=p.prompt_version,
                test_plan_id=p.id,
                agent_name=p.agent.name if p.agent else None,
                user_username=p.created_by_user.username if p.created_by_user else None,
                user_full_name=(p.created_by_user.full_name or None) if p.created_by_user else None,
                agent_session_id=p.agent_session_id,
            ))

    if "execution" in want:
        # ExecutionSession is plan-scoped, not project-scoped.  Join
        # through TestPlan to get the project filter.
        q = (
            db.query(ExecutionSession)
            .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
            .filter(TestPlan.project_id == project_id)
        )
        q = _apply_execution_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for e in q.order_by(ExecutionSession.started_at.desc()).limit(per_kind_cap).all():
            rows.append(AgentSessionRow(
                kind="execution",
                id=e.id,
                project_id=project_id,  # joined from test_plan above
                agent_id=e.agent_id,
                user_id=e.started_by_id,
                status=e.status,
                started_at=e.started_at,
                completed_at=e.completed_at,
                generated_by_model=e.generated_by_model,
                generated_by_tool=e.generated_by_tool,
                prompt_version=e.prompt_version,
                test_plan_id=e.test_plan_id,
                agent_name=e.agent.name if e.agent else None,
                user_username=e.started_by.username if e.started_by else None,
                user_full_name=(e.started_by.full_name or None) if e.started_by else None,
                agent_session_id=e.agent_session_id,
            ))

    if "assist" in want:
        # v2.303.0. Assist is project-scoped — no plan, no scope — so both
        # target ids stay null. `purpose` (the operator's stated reason for
        # the session) has no home on the shared row shape; the assist-session
        # surface at /assist-sessions carries it.
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
                test_plan_id=None,
                agent_name=a.agent.name if a.agent else None,
                user_username=a.started_by.username if a.started_by else None,
                user_full_name=(a.started_by.full_name or None) if a.started_by else None,
                agent_session_id=a.agent_session_id,
            ))

    _attach_target_labels(db, rows)

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
    _attach_session_liveness(db, page)
    return page


def summarise_by_model_tool(
    db: Session,
    project_id: int,
) -> List[dict]:
    """Aggregate sessions by ``(generated_by_model, generated_by_tool)``
    for the v3 per-model rollup card.

    Returns one dict per tuple with counts of each kind so the UI
    can render "claude-opus-4-7 / claude-code: 3 recon, 2 plans,
    5 executions".  Rows with null model+tool are folded into the
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
            "plan_generation": 0,
            "execution": 0,
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

    plan_rows = (
        db.query(
            TestPlan.generated_by_model,
            TestPlan.generated_by_tool,
            _func.count(TestPlan.id),
        )
        .filter(TestPlan.project_id == project_id)
        .group_by(TestPlan.generated_by_model, TestPlan.generated_by_tool)
        .all()
    )
    for model, tool, n in plan_rows:
        _bucket(model, tool, "plan_generation", n)

    exec_rows = (
        db.query(
            ExecutionSession.generated_by_model,
            ExecutionSession.generated_by_tool,
            _func.count(ExecutionSession.id),
        )
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .filter(TestPlan.project_id == project_id)
        .group_by(ExecutionSession.generated_by_model, ExecutionSession.generated_by_tool)
        .all()
    )
    for model, tool, n in exec_rows:
        _bucket(model, tool, "execution", n)

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
