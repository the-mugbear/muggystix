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
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    ExecutionSession,
    ExecutionSessionStatus,
    ReconSession,
    ReconSessionStatus,
    TestPlan,
)
from app.db.models_auth import APIKey, User
from app.services.agent_key_ttl import resolve_expires_at

logger = logging.getLogger(__name__)


# v2.337.0 — ``project`` is the consolidated kind every new session gets; the
# four legacy kinds remain so sessions started before the consolidation keep
# showing up (they are sourced from their detail tables, keyed by those ids).
SessionKind = Literal["project", "recon", "plan_generation", "execution", "assist"]

#: The default kind set. Named once so the call sites can't drift — assist
#: was once missing from three of five copies of this list.
ALL_SESSION_KINDS = {"project", "recon", "plan_generation", "execution", "assist"}

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
    so tests and backfills can construct historical shapes.
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


def active_recon_phases(db: Session, session_id: int) -> List[ReconSession]:
    return (
        db.query(ReconSession)
        .filter(
            ReconSession.agent_session_id == session_id,
            ReconSession.status == ReconSessionStatus.ACTIVE.value,
        )
        .order_by(ReconSession.id)
        .all()
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


def resolve_recon_phase(
    db: Session,
    session_id: int,
    recon_session_id: Optional[int] = None,
) -> ReconSession:
    """The recon run a call is about.

    An explicit ``recon_session_id`` must belong to this session — a run
    opened by another session is not the caller's to write into (that is the
    record-integrity boundary the per-key scope used to carry).  Without
    one, the session's single active run is meant; none open is a 409 that
    names the start call, and more than one is a 400 that lists them.
    """
    if recon_session_id is not None:
        row = (
            db.query(ReconSession)
            .filter(
                ReconSession.id == recon_session_id,
                ReconSession.agent_session_id == session_id,
            )
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Recon run #{recon_session_id} was not opened by this session. "
                    "Pass a recon_session_id from POST /agent/recon/start, or "
                    "omit it to use this session's single active run."
                ),
            )
        return row
    active = active_recon_phases(db, session_id)
    if len(active) == 1:
        return active[0]
    if not active:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_active_recon_run",
                "message": (
                    "This session has no active reconnaissance run. Open one "
                    "with POST /agent/recon/start {\"scope_id\": ...} (the "
                    "scopes are at GET /agent/scopes), then retry."
                ),
            },
        )
    raise HTTPException(
        status_code=400,
        detail={
            "error": "ambiguous_recon_run",
            "message": (
                "This session has more than one active reconnaissance run; "
                "say which with recon_session_id."
            ),
            "recon_session_ids": [r.id for r in active],
        },
    )


def resolve_execution_phase(
    db: Session,
    session_id: int,
    execution_session_id: Optional[int] = None,
    *,
    plan_id: Optional[int] = None,
) -> ExecutionSession:
    """The execution run a call is about — by id, by plan, or the single
    active one.  Same ownership rule as :func:`resolve_recon_phase`."""
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
                        "{\"plan_id\": " + str(plan_id) + "} — the plan must be approved."
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


def open_recon_phase(
    db: Session,
    *,
    session: AgentSession,
    scope,
    notes: Optional[str] = None,
) -> ReconSession:
    """Open a reconnaissance run on ``scope`` within ``session``.

    One active run per scope per session — a second start on the same scope
    returns the run already open rather than fragmenting its counters.  The
    session's environment probe is snapshotted onto the run so
    ``/recon/context`` can shape its recommended sequence to this operator's
    machine.
    """
    existing = (
        db.query(ReconSession)
        .filter(
            ReconSession.agent_session_id == session.id,
            ReconSession.scope_id == scope.id,
            ReconSession.status == ReconSessionStatus.ACTIVE.value,
        )
        .first()
    )
    if existing is not None:
        return existing
    run = ReconSession(
        project_id=session.project_id,
        scope_id=scope.id,
        agent_id=session.agent_id,
        started_by_id=session.started_by_id,
        status=ReconSessionStatus.ACTIVE.value,
        notes=(notes or "").strip() or None,
        agent_session_id=session.id,
    )
    _copy_probe(session, run)
    db.add(run)
    db.flush()
    return run


def open_execution_phase(
    db: Session,
    *,
    session: AgentSession,
    plan: TestPlan,
) -> ExecutionSession:
    """Open (or resume) an execution run on ``plan`` within ``session``.

    The plan must be approved (or already in progress) and non-empty — the
    human approval gate is the one control this consolidation keeps intact.
    At most one run per plan is active at a time: a run this session
    already has open on the plan is reused; any other session's active run
    is paused, as ``/execute`` always did.  Raises HTTPException.
    """
    if plan.project_id != session.project_id:
        raise HTTPException(status_code=404, detail="Test plan not found")
    # Serialise concurrent opens on the plan row (Postgres FOR UPDATE; a no-op
    # on SQLite, where the partial-unique index is the backstop).
    db.query(TestPlan).filter(TestPlan.id == plan.id).with_for_update().first()
    if plan.status not in ("approved", "in_progress"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan #{plan.id} is {plan.status}; execution requires an approved "
                "plan. Submit it and ask the operator to approve it first."
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
    if plan.status == "approved":
        plan.status = "in_progress"
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
    for run in active_recon_phases(db, session.id):
        _copy_probe(session, run)
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
    recon = active_recon_phases(db, session.id)
    execs = active_execution_phases(db, session.id)
    # The plan a bare ``plan_id`` means: the active execution's plan, else the
    # newest plan this session drafted.
    plan_id: Optional[int] = None
    if len(execs) == 1:
        plan_id = execs[0].test_plan_id
    else:
        latest_plan = (
            db.query(TestPlan.id)
            .filter(TestPlan.agent_session_id == session.id)
            .order_by(TestPlan.id.desc())
            .first()
        )
        if latest_plan is not None:
            plan_id = latest_plan[0]
    drafted = [
        pid for (pid,) in db.query(TestPlan.id)
        .filter(TestPlan.agent_session_id == session.id)
        .order_by(TestPlan.id)
        .all()
    ]
    return {
        "recon_session_id": recon[0].id if len(recon) == 1 else None,
        "active_recon_session_ids": [r.id for r in recon],
        "execution_session_id": execs[0].id if len(execs) == 1 else None,
        "active_execution_session_ids": [e.id for e in execs],
        "plan_id": plan_id,
        "drafted_plan_ids": drafted,
    }


def end_agent_session(
    db: Session,
    session: AgentSession,
    *,
    ended_by: Optional[User] = None,
    reason: Optional[str] = None,
) -> None:
    """End a session: revoke its keys and close what it left open.

    Active recon runs are marked abandoned (nothing can upload into them
    again) and active execution runs are paused (a later session may open
    a fresh run on the same plan and continue from the recorded results).
    Draft plans stay drafts — they are project data, not session state.
    """
    now = datetime.now(timezone.utc)
    who = (ended_by.full_name or ended_by.username) if ended_by is not None else "system"
    line = f"[{now.isoformat()}] Session ended by {who}" + (f": {reason}" if reason else "")

    revoke_session_keys(db, session.id)
    for run in active_recon_phases(db, session.id):
        run.status = ReconSessionStatus.ABANDONED.value
        run.completed_at = now
        run.notes = (f"{run.notes}\n{line}" if run.notes else line)[-8192:]
    for run in active_execution_phases(db, session.id):
        run.status = ExecutionSessionStatus.PAUSED.value
        run.notes = (f"{run.notes}\n{line}" if run.notes else line)[-8192:]
    session.status = SESSION_ENDED
    session.completed_at = now
    session.notes = (f"{session.notes}\n{line}" if session.notes else line)[-8192:]


def has_live_session_key(now: Optional[datetime] = None):
    """Correlated EXISTS against the outer ``AgentSession``: an unexpired
    active key.  Use for filters; fetch the value to display separately."""
    from sqlalchemy import select
    now = now or datetime.now(timezone.utc)
    return (
        select(APIKey.id)
        .where(
            APIKey.agent_session_id == AgentSession.id,
            APIKey.is_active.is_(True),
            APIKey.expires_at.isnot(None),
            APIKey.expires_at > now,
        )
        .exists()
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


def effective_session_status(
    stored_status: str, key_expires_at: Optional[datetime], now: Optional[datetime] = None,
) -> str:
    """``active`` only while a key can still be used; the stored column is
    converged hourly by :func:`lapse_expired_agent_sessions`."""
    if stored_status != SESSION_ACTIVE:
        return stored_status
    now = now or datetime.now(timezone.utc)
    if key_expires_at is None or key_expires_at <= now:
        return SESSION_ENDED
    return stored_status


def lapse_expired_agent_sessions(db: Session) -> int:
    """End every active session whose keys have all expired past renewal.

    A key can be renewed after expiry while its session is under the maximum
    lifetime (v2.304.0), so a session is lapsed only once its renewal
    deadline has also passed — otherwise the sweep would kill a session whose
    agent is mid-scan and about to renew.  Returns the number ended.
    """
    from app.api.deps import session_renewal_deadline

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
        end_agent_session(db, session, reason="keys expired past the renewal window")
        # The truthful timestamp is when access actually stopped.
        session.completed_at = expires_at or session.completed_at
        lapsed.append(session)
    # Legacy assist detail rows mirror the status so their review page agrees.
    if lapsed:
        ids = [s.id for s in lapsed]
        db.query(AssistSession).filter(
            AssistSession.agent_session_id.in_(ids),
            AssistSession.status == "active",
        ).update({"status": "ended", "ended_at": now}, synchronize_session=False)
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
    least-common-denominator the v3 UI consumes.  ``scope_id`` is
    populated for recon kind only; ``test_plan_id`` is populated
    for plan_generation + execution.  ``status`` is each session's
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
    scope_id: Optional[int]
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
            "scope_id": self.scope_id,
            "test_plan_id": self.test_plan_id,
            "agent_name": self.agent_name,
            "user_username": self.user_username,
            "target_label": self.target_label,
            "purpose": self.purpose,
        }


def _apply_recon_filters(q, *, agent_id, model, tool, user_id, status):
    if agent_id is not None:
        q = q.filter(ReconSession.agent_id == agent_id)
    if model is not None:
        q = q.filter(ReconSession.generated_by_model == model)
    if tool is not None:
        q = q.filter(ReconSession.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(ReconSession.started_by_id == user_id)
    if status is not None:
        q = q.filter(ReconSession.status == status)
    return q


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
    return q


def _plan_generation_status(plan_status: str) -> str:
    """Collapse TestPlan.status to the agent-generation lifecycle.

    v2.45.2 — agent_session_service used to pass TestPlan.status
    through verbatim, which conflated the GENERATION lifecycle
    (DRAFT → PROPOSED) with the EXECUTION lifecycle (APPROVED →
    IN_PROGRESS → COMPLETED).  A plan that the agent submitted
    successfully and the user then approved + executed would show
    in the plan_generation timeline as "in_progress", even though
    the generation agent's work concluded at /submit time.

    Mapping:
      DRAFT     → "in_progress"   (agent is filling in entries)
      PROPOSED  → "submitted"      (agent done; awaiting human review)
      APPROVED, IN_PROGRESS, COMPLETED  → "completed"
        (generation conclusively done — downstream is execution,
        tracked via the execution-session row not this one)
      REJECTED  → "rejected"       (terminal — human declined)
      ARCHIVED  → "archived"       (terminal — long-tail)
      anything else (unknown enum value): passed through unchanged,
        so future TestPlanStatus additions don't silently become
        "in_progress".
    """
    if plan_status == "draft":
        return "in_progress"
    if plan_status == "proposed":
        return "submitted"
    if plan_status in ("approved", "in_progress", "completed"):
        return "completed"
    if plan_status == "rejected":
        return "rejected"
    if plan_status == "archived":
        return "archived"
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
    return q


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
    return q


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

    if "recon" in want:
        q = db.query(_func.count(ReconSession.id)).filter(
            ReconSession.project_id == project_id
        )
        q = _apply_recon_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
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


#: CIDRs listed in a target label before it truncates. A scope with 40 subnets
#: should identify itself, not fill the row.
_TARGET_CIDR_CAP = 3


def _attach_target_labels(db: Session, rows: "List[AgentSessionRow]") -> None:
    """Fill ``target_label`` for a page of rows, in two queries.

    v2.306.0.  Batched deliberately: this runs on the Agent Runs list, and a
    per-row lookup would put the timeline back into N+1 for a purely cosmetic
    field. Two IN() queries regardless of page size.
    """
    from app.db import models

    scope_ids = {r.scope_id for r in rows if r.scope_id is not None}
    plan_ids = {r.test_plan_id for r in rows if r.test_plan_id is not None}

    scope_labels: dict[int, str] = {}
    if scope_ids:
        names = dict(
            db.query(models.Scope.id, models.Scope.name)
            .filter(models.Scope.id.in_(scope_ids)).all()
        )
        cidrs: dict[int, List[str]] = {}
        for sid, cidr in (
            db.query(models.Subnet.scope_id, models.Subnet.cidr)
            .filter(models.Subnet.scope_id.in_(scope_ids))
            .order_by(models.Subnet.cidr)
            .all()
        ):
            cidrs.setdefault(sid, []).append(cidr)
        for sid in scope_ids:
            ranges = cidrs.get(sid, [])
            shown = ", ".join(ranges[:_TARGET_CIDR_CAP])
            if len(ranges) > _TARGET_CIDR_CAP:
                shown += f" +{len(ranges) - _TARGET_CIDR_CAP} more"
            name = names.get(sid)
            # The CIDRs are the part another analyst needs; the scope name is
            # context. Show both when they differ, ranges alone when there is
            # no name to add.
            scope_labels[sid] = f"{name} — {shown}" if name and shown else (shown or name or "")

    plan_labels: dict[int, str] = {}
    if plan_ids:
        plan_labels = dict(
            db.query(TestPlan.id, TestPlan.title)
            .filter(TestPlan.id.in_(plan_ids)).all()
        )

    for r in rows:
        if r.kind == "project":
            continue  # labelled from its phases below
        if r.scope_id is not None:
            r.target_label = scope_labels.get(r.scope_id) or None
        elif r.test_plan_id is not None:
            r.target_label = plan_labels.get(r.test_plan_id) or None
        # assist: project-wide by design — no target, and saying so is the UI's
        # job, not a fake label here.

    # v2.337.0 — a consolidated session declares its targets per phase.  Three
    # grouped queries for the page, then one line per session: the scopes it
    # scanned, the plans it drafted, the plans it executed.
    project_ids = [r.id for r in rows if r.kind == "project"]
    if not project_ids:
        return
    recon_by: dict[int, List[str]] = {}
    for sid, scope_id in (
        db.query(ReconSession.agent_session_id, ReconSession.scope_id)
        .filter(ReconSession.agent_session_id.in_(project_ids))
        .order_by(ReconSession.id)
        .all()
    ):
        recon_by.setdefault(sid, []).append(str(scope_id))
    recon_scope_ids = {int(s) for v in recon_by.values() for s in v}
    if recon_scope_ids:
        cidrs2: dict[int, List[str]] = {}
        for scope_id, cidr in (
            db.query(models.Subnet.scope_id, models.Subnet.cidr)
            .filter(models.Subnet.scope_id.in_(recon_scope_ids))
            .order_by(models.Subnet.cidr)
            .all()
        ):
            cidrs2.setdefault(scope_id, []).append(cidr)
        for sid, ids in recon_by.items():
            parts = []
            for s in ids:
                ranges = cidrs2.get(int(s), [])
                shown = ", ".join(ranges[:_TARGET_CIDR_CAP])
                if len(ranges) > _TARGET_CIDR_CAP:
                    shown += f" +{len(ranges) - _TARGET_CIDR_CAP} more"
                parts.append(shown or f"scope #{s}")
            recon_by[sid] = parts
    drafted_by: dict[int, List[str]] = {}
    for sid, title in (
        db.query(TestPlan.agent_session_id, TestPlan.title)
        .filter(TestPlan.agent_session_id.in_(project_ids))
        .order_by(TestPlan.id)
        .all()
    ):
        drafted_by.setdefault(sid, []).append(title)
    executed_by: dict[int, List[str]] = {}
    for sid, title in (
        db.query(ExecutionSession.agent_session_id, TestPlan.title)
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .filter(ExecutionSession.agent_session_id.in_(project_ids))
        .order_by(ExecutionSession.id)
        .all()
    ):
        executed_by.setdefault(sid, []).append(title)
    for r in rows:
        if r.kind != "project":
            continue
        parts: List[str] = []
        if r.id in recon_by:
            parts.append("recon " + "; ".join(recon_by[r.id]))
        if r.id in drafted_by:
            parts.append("drafted " + "; ".join(drafted_by[r.id]))
        if r.id in executed_by:
            parts.append("executed " + "; ".join(executed_by[r.id]))
        r.target_label = " · ".join(parts) or None


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
) -> List[AgentSessionRow]:
    """Return the unified agent-session list for a project.

    Filters are AND'd.  Ordering is started_at DESC (most recent first)
    with ``id`` + ``kind`` as deterministic tiebreakers.

    ``status`` matches each kind's native status column (recon +
    execution use 'active' / 'paused' / 'completed' / 'failed' /
    'abandoned'; plan_generation uses TestPlan.status —
    'draft' / 'pending_review' / 'approved' / 'in_progress' /
    'completed' / 'rejected').  Pass 'active' for the in-flight
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
                scope_id=None,
                test_plan_id=None,
                agent_name=s.agent.name if s.agent else None,
                user_username=s.started_by.username if s.started_by else None,
                purpose=s.purpose,
            ))

    if "recon" in want:
        q = db.query(ReconSession).filter(ReconSession.project_id == project_id)
        q = _apply_recon_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for s in q.order_by(ReconSession.started_at.desc()).limit(per_kind_cap).all():
            rows.append(AgentSessionRow(
                kind="recon",
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
                scope_id=s.scope_id,
                test_plan_id=None,
                agent_name=s.agent.name if s.agent else None,
                user_username=s.started_by.username if s.started_by else None,
            ))

    if "plan_generation" in want:
        # Plan creation is the closest analogue to a "session" for the
        # plan-generation workflow.  Each TestPlan row is one
        # creation event; the timeline uses ``created_at`` as the
        # started_at.
        #
        # v2.45.2 — the displayed status is GENERATION-BOUNDED, not
        # the plan's full lifecycle.  Pre-fix the row passed
        # ``p.status`` through unchanged, so a plan that had been
        # submitted, approved, AND moved to execution would show
        # the agent's plan-generation session as "in_progress" —
        # conflating execution state with generation work that
        # actually finished at /submit time.  The mapping below
        # collapses every post-PROPOSED state to "completed"
        # because the agent's job ends when the plan moves out of
        # DRAFT; downstream lifecycle is the user's concern via
        # the execution surface (which has its own session rows).
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
                scope_id=None,
                test_plan_id=p.id,
                agent_name=p.agent.name if p.agent else None,
                user_username=p.created_by_user.username if p.created_by_user else None,
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
                scope_id=None,
                test_plan_id=e.test_plan_id,
                agent_name=e.agent.name if e.agent else None,
                user_username=e.started_by.username if e.started_by else None,
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
                scope_id=None,
                test_plan_id=None,
                agent_name=a.agent.name if a.agent else None,
                user_username=a.started_by.username if a.started_by else None,
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
    return rows[offset : offset + limit]


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
            "recon": 0,
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

    recon_rows = (
        db.query(
            ReconSession.generated_by_model,
            ReconSession.generated_by_tool,
            _func.count(ReconSession.id),
        )
        .filter(ReconSession.project_id == project_id)
        .group_by(ReconSession.generated_by_model, ReconSession.generated_by_tool)
        .all()
    )
    for model, tool, n in recon_rows:
        _bucket(model, tool, "recon", n)

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
