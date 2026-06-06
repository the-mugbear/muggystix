"""
JWT-facing execution-session lookup by ID (v3 alpha.7).

The existing surface under ``/test-plans/{plan_id}/execution-sessions/``
is plan-scoped: it requires the caller to already know the plan ID.
The v3 ``/executions/:sessionId`` page reaches sessions by session
ID alone (a permalink — alpha.4 set the components up for this), so
it needs a project-scoped lookup that resolves plan + bundle in one
call.

This module adds:

* ``GET /projects/{id}/execution-sessions/{session_id}`` —
  full bundle (session metadata + every entry's per-test results +
  sanity checks).  Identical payload shape to the plan-scoped
  ``/test-plans/{plan_id}/execution-sessions/{session_id}/all-entry-results``
  endpoint but addressable without knowing the plan, so the v3
  ExecutionDetail page can render from just the URL.

Why not just expand the plan-scoped one?  Two reasons:

1. The plan ID would be redundant in the URL when the session ID
   already pins down the plan transitively (sessions belong to
   exactly one plan).  Making the user know both creates fragile
   URLs.
2. The v3 nav routes sessions as first-class items (``/executions/:id``,
   like ``/recon/runs/:id``).  A project-scoped endpoint matches
   that mental model.

The plan-scoped endpoint stays for the TestPlanDetail UI flow that
already knows the plan ID — both paths produce the same shape, so
the page can switch between them transparently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.schemas.pagination import Paginated
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_project, require_project_role, is_project_admin
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_agent import (
    ExecutionSession,
    ExecutionSessionStatus,
    HostSanityCheck,
    TestExecutionResult,
    TestPlan,
    TestPlanEntry,
)
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db


router = APIRouter()


# ---------------------------------------------------------------------------
# Summary row shape for the LIST endpoint (v3 alpha.12).
# Lighter than the detail bundle — designed for a project-wide list
# page (filterable by status / plan / model / user).
# ---------------------------------------------------------------------------

class ExecutionSessionRow(BaseModel):
    id: int
    test_plan_id: int
    plan_title: Optional[str] = None
    plan_version: Optional[int] = None
    status: str
    mode: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    started_by_username: Optional[str] = None
    agent_name: Optional[str] = None
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    # Per-session result + finding counters so the list can show
    # "12 tests, 3 findings" without a per-row drill-in.
    result_count: int = 0
    finding_count: int = 0


# Reuse the response model + row builders from the test_plans module
# so the two endpoints emit identical payloads.  Imported lazily inside
# the handler to avoid a circular import (test_plans imports from this
# router-set indirectly through __init__).
def _build_bundle(
    db: Session,
    session: ExecutionSession,
    entries_skip: int = 0,
    entries_limit: Optional[int] = None,
):
    """Assemble the AllEntryResultsResponse for one session.

    Duplicated logic from ``get_all_entry_results`` rather than
    importing the handler — keeps this endpoint independent of
    request shape changes in the plan-scoped one.  The shape is
    pinned by the shared Pydantic model.

    v2.86.7 — accepts pagination args.  Pre-fix the helper did
    ``.all()`` over every entry of the plan; for thousand-entry
    sessions that produced a multi-MB payload on every
    ExecutionDetail page load.  ``entries_limit=None`` keeps the
    pre-fix back-compat behaviour (return every entry).  Also added
    ``selectinload(TestPlanEntry.host)`` so the per-row
    ``e.host.ip_address`` access below doesn't fire a query per
    entry — the existing code at line 156 was an N+1.
    """
    from app.api.v1.endpoints.test_plans import (
        AllEntryResultsResponse,
        EntryResultsBundle,
        HostSanityCheckRow,
        TestExecutionResultRow,
    )

    entries_total = (
        db.query(func.count(TestPlanEntry.id))
        .filter(TestPlanEntry.test_plan_id == session.test_plan_id)
        .scalar()
        or 0
    )
    entries_q = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == session.test_plan_id)
        .options(selectinload(TestPlanEntry.host))
        .order_by(TestPlanEntry.id.asc())
        .offset(entries_skip)
    )
    if entries_limit is not None:
        entries_q = entries_q.limit(entries_limit)
    entries = entries_q.all()
    entry_ids = [e.id for e in entries]
    tests_by_entry: dict = {}
    if entry_ids:
        for t in (
            db.query(TestExecutionResult)
            .filter(
                TestExecutionResult.execution_session_id == session.id,
                TestExecutionResult.entry_id.in_(entry_ids),
            )
            .order_by(TestExecutionResult.entry_id, TestExecutionResult.test_index)
            .all()
        ):
            tests_by_entry.setdefault(t.entry_id, []).append(t)

    checks_by_entry: dict = {}
    if entry_ids:
        for c in (
            db.query(HostSanityCheck)
            .filter(
                HostSanityCheck.execution_session_id == session.id,
                HostSanityCheck.entry_id.in_(entry_ids),
            )
            .order_by(HostSanityCheck.entry_id, HostSanityCheck.checked_at)
            .all()
        ):
            checks_by_entry.setdefault(c.entry_id, []).append(c)

    return AllEntryResultsResponse(
        plan_id=session.test_plan_id,
        execution_session_id=session.id,
        execution_session_status=session.status,
        started_at=session.started_at,
        completed_at=session.completed_at,
        started_by_username=(
            session.started_by.username if session.started_by else None
        ),
        agent_name=session.agent.name if session.agent else None,
        generated_by_model=session.generated_by_model,
        generated_by_tool=session.generated_by_tool,
        prompt_version=session.prompt_version,
        entries=[
            EntryResultsBundle(
                entry_id=e.id,
                host_id=e.host_id,
                host_ip=e.host.ip_address if e.host else None,
                host_hostname=e.host.hostname if e.host else None,
                entry_status=e.status,
                tests=[
                    TestExecutionResultRow.model_validate(t)
                    for t in tests_by_entry.get(e.id, [])
                ],
                sanity_checks=[
                    HostSanityCheckRow.model_validate(c)
                    for c in checks_by_entry.get(e.id, [])
                ],
            )
            for e in entries
        ],
        entries_total=entries_total,
        entries_skip=entries_skip if entries_limit is not None else None,
        entries_limit=entries_limit,
    )


@router.get(
    "/{session_id}",
    summary="Full execution session by id (v3 alpha.7)",
)
def get_execution_session(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    entries_skip: int = Query(
        0,
        ge=0,
        description="Offset into the session's entries list (v2.86.7).",
    ),
    entries_limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Cap on how many entries to return.  Back-compat default "
            "(None) returns every entry — fine on sessions ≤ a few "
            "hundred entries, expensive on thousand-entry plans.  "
            "ExecutionDetail callers should paginate; pass entries_limit "
            "and use entries_total to drive a 'load more' affordance "
            "(v2.86.7)."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return the full execution-session bundle (session metadata +
    per-entry results + sanity checks) for one session in this
    project, looked up by session id alone.

    Joins through TestPlan to enforce project scoping — a session
    belonging to a plan in another project returns the actionable
    404 used by ``get_test_plan`` / ``get_recon_session``.
    """
    session = (
        db.query(ExecutionSession)
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .filter(
            ExecutionSession.id == session_id,
            TestPlan.project_id == project.id,
        )
        .first()
    )
    if not session:
        # Disambiguate "doesn't exist anywhere" vs "exists in another
        # project" so the user gets actionable feedback.
        other = (
            db.query(TestPlan.project_id)
            .join(ExecutionSession, ExecutionSession.test_plan_id == TestPlan.id)
            .filter(ExecutionSession.id == session_id)
            .first()
        )
        if other:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Execution session #{session_id} belongs to a plan "
                    f"in a different project (project #{other[0]}). "
                    f"Switch to that project to view it."
                ),
            )
        raise HTTPException(status_code=404, detail="Execution session not found")

    return _build_bundle(db, session, entries_skip=entries_skip, entries_limit=entries_limit)


@router.get(
    "/",
    response_model=Paginated[ExecutionSessionRow],
    summary="List execution sessions for this project (v3 alpha.12)",
)
def list_execution_sessions(
    project_id: int = Path(..., gt=0),
    status: Optional[str] = Query(
        None,
        description=(
            "Filter by ExecutionSessionStatus — 'active' / 'paused' / "
            "'completed' / 'failed' / 'abandoned'.  Omit to get all."
        ),
    ),
    test_plan_id: Optional[int] = Query(
        None, gt=0, description="Filter to one plan's executions."
    ),
    model: Optional[str] = Query(
        None, description="Filter by generated_by_model."
    ),
    user_id: Optional[int] = Query(
        None, description="Filter to one user's executions."
    ),
    search: Optional[str] = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match on model / tool / "
            "prompt_version / TestPlan title (v2.86.10)."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return the project's execution sessions, newest-started first.

    Project-scoping joins through TestPlan since ExecutionSession
    only knows about test_plan_id directly.  Drives the v3 alpha.12
    ``/executions`` list page and the multi-select Compare flow.

    Per-session result + finding counts are computed in batch so the
    UI doesn't N+1 against the results table per row.

    v2.86.10 — paginated.

    v2.86.13 — response shape standardised on ``Paginated[T]``
    (``{items, total, skip, limit, has_more}``).  Drops the
    transitional ``X-Total-Count`` header in favour of the
    in-body total.
    """
    q = (
        db.query(ExecutionSession, TestPlan)
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .filter(TestPlan.project_id == project.id)
    )
    if status:
        q = q.filter(ExecutionSession.status == status)
    if test_plan_id is not None:
        q = q.filter(ExecutionSession.test_plan_id == test_plan_id)
    if model:
        q = q.filter(ExecutionSession.generated_by_model == model)
    if user_id is not None:
        q = q.filter(ExecutionSession.started_by_id == user_id)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        q = q.filter(
            or_(
                ExecutionSession.generated_by_model.ilike(like),
                ExecutionSession.generated_by_tool.ilike(like),
                ExecutionSession.prompt_version.ilike(like),
                TestPlan.title.ilike(like),
            )
        )

    total = q.with_entities(func.count(ExecutionSession.id)).scalar() or 0
    rows = (
        q.order_by(ExecutionSession.started_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    if not rows:
        return Paginated[ExecutionSessionRow].build(
            items=[], total=total, skip=skip, limit=limit,
        )

    session_ids = [s.id for s, _ in rows]

    # Result counts in one batch query.
    result_counts = dict(
        db.query(
            TestExecutionResult.execution_session_id,
            func.count(TestExecutionResult.id),
        )
        .filter(TestExecutionResult.execution_session_id.in_(session_ids))
        .group_by(TestExecutionResult.execution_session_id)
        .all()
    )
    finding_counts = dict(
        db.query(
            TestExecutionResult.execution_session_id,
            func.count(TestExecutionResult.id),
        )
        .filter(
            TestExecutionResult.execution_session_id.in_(session_ids),
            TestExecutionResult.is_finding.is_(True),
        )
        .group_by(TestExecutionResult.execution_session_id)
        .all()
    )

    items = [
        ExecutionSessionRow(
            id=s.id,
            test_plan_id=s.test_plan_id,
            plan_title=plan.title if plan else None,
            plan_version=plan.version if plan else None,
            status=s.status,
            mode=s.mode,
            started_at=s.started_at,
            completed_at=s.completed_at,
            started_by_username=(
                s.started_by.username if s.started_by else None
            ),
            agent_name=s.agent.name if s.agent else None,
            generated_by_model=s.generated_by_model,
            generated_by_tool=s.generated_by_tool,
            prompt_version=s.prompt_version,
            result_count=int(result_counts.get(s.id, 0) or 0),
            finding_count=int(finding_counts.get(s.id, 0) or 0),
        )
        for s, plan in rows
    ]
    return Paginated[ExecutionSessionRow].build(
        items=items, total=total, skip=skip, limit=limit,
    )


# ---------------------------------------------------------------------------
# Abandon — operator-driven stuck-session cleanup (v4 beta.7)
# ---------------------------------------------------------------------------

class ExecutionAbandonRequest(BaseModel):
    """Optional reason the operator typed in the confirmation dialog.

    Capped server-side so a malicious actor can't blow up the notes
    column.  Empty reason is fine — the audit line still records who
    abandoned and when.
    """
    reason: Optional[str] = Field(
        default=None,
        max_length=512,
        description="Short free-form explanation (e.g. 'agent crashed mid-plan').",
    )


@router.post(
    "/{session_id}/abandon",
    response_model=ExecutionSessionRow,
    summary="Mark a stuck execution session as abandoned (v4 beta.7)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def abandon_execution_session(
    body: ExecutionAbandonRequest,
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """Operator-driven escape hatch for execution sessions whose agent
    never reached the terminal state (e.g. agent process died, user
    killed the terminal, the agent hung mid-test).  Mirrors the recon
    abandon endpoint — same auth gate, same audit-line shape, same
    409-on-terminal-state guard.

    Requires the analyst role.  Only valid for sessions in 'active'
    or 'paused' state; a session already in a terminal state
    ('completed' / 'failed' / 'abandoned') returns 409 so accidental
    double-abandons don't silently rewrite metadata.

    Any results already submitted by the agent stay; this only
    transitions the session row, so consumers can see "abandoned
    after 4 of 12 entries" rather than the session sitting at
    'active' forever and the AgentActivityRail keeping it as live.
    """
    # Project-scope by joining through TestPlan since sessions belong
    # to plans, not projects directly.
    session = (
        db.query(ExecutionSession)
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .filter(
            ExecutionSession.id == session_id,
            TestPlan.project_id == project.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=404, detail="Execution session not found"
        )

    # v2.45.9 — ownership gate (mirrors the recon abandon endpoint).
    # The analyst project-role lets a user close THEIR OWN stuck
    # sessions; abandoning another operator's session requires
    # project-admin (the departed-user-cleanup recovery path).  An
    # owner-less session (started_by_id NULL after a user delete) is
    # orphaned — any analyst may reclaim it.
    if (
        session.started_by_id is not None
        and session.started_by_id != user.id
        and not is_project_admin(db, project.id, user)
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "This execution session belongs to another operator. Only "
                "its owner or a project admin can abandon it."
            ),
        )

    terminal = {
        ExecutionSessionStatus.COMPLETED.value,
        ExecutionSessionStatus.ABANDONED.value,
        # v2.43.3 (AUD-N1): FAILED was added to the enum in this release
        # — referenced symbolically now instead of the literal "failed"
        # string the pre-fix comment had to apologize for.
        ExecutionSessionStatus.FAILED.value,
    }
    if session.status in terminal:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Execution session #{session_id} is already in terminal "
                f"state '{session.status}'; only active or paused sessions "
                f"can be abandoned."
            ),
        )

    now = datetime.now(timezone.utc)
    header = f"[Abandoned by {user.username} on {now.isoformat()}]"
    audit_line = header if not body.reason else f"{header}: {body.reason.strip()}"
    session.notes = (
        f"{session.notes}\n\n{audit_line}" if session.notes else audit_line
    )[:8192]
    session.status = ExecutionSessionStatus.ABANDONED.value
    session.completed_at = now
    db.commit()
    db.refresh(session)

    plan = (
        db.query(TestPlan)
        .filter(TestPlan.id == session.test_plan_id)
        .first()
    )
    result_count = (
        db.query(func.count(TestExecutionResult.id))
        .filter(TestExecutionResult.execution_session_id == session.id)
        .scalar()
        or 0
    )
    finding_count = (
        db.query(func.count(TestExecutionResult.id))
        .filter(
            TestExecutionResult.execution_session_id == session.id,
            TestExecutionResult.is_finding.is_(True),
        )
        .scalar()
        or 0
    )
    return ExecutionSessionRow(
        id=session.id,
        test_plan_id=session.test_plan_id,
        plan_title=plan.title if plan else None,
        plan_version=plan.version if plan else None,
        status=session.status,
        mode=session.mode,
        started_at=session.started_at,
        completed_at=session.completed_at,
        started_by_username=(
            session.started_by.username if session.started_by else None
        ),
        agent_name=session.agent.name if session.agent else None,
        generated_by_model=session.generated_by_model,
        generated_by_tool=session.generated_by_tool,
        prompt_version=session.prompt_version,
        result_count=int(result_count),
        finding_count=int(finding_count),
    )
