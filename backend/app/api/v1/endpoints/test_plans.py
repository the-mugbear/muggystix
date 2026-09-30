"""
Test Plan Endpoints (User-Facing)

View, create, edit and archive test plans — the record of what an agent
(or a person) set out to test and what it found.  There is no approval step
and no per-plan agent key (v2.433.0): the operator starts one agent session
and the agent opens its own planning and execution work within it.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.session import get_db
from app.db.models import Host
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_agent import (
    TestPlan, TestPlanEntry,
    TestPlanHistory, TestPlanStatus, PLANNED_PLAN_STATUSES,
    TestEntryPriority, TestPhase,
    ExecutionSession, ExecutionSessionStatus,
    TestExecutionResult, HostSanityCheck, AgentApiCall,
)
from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.services.test_plan_service import TestPlanService
from app.schemas.schemas import StoredProposedTestItem
# Shared test-plan schemas live in app/schemas/test_plan_schemas.py (CLAUDE.md
# file-size policy).  Single-use response models stay inline below, next to
# the one endpoint that returns them.
from app.schemas.test_plan_schemas import (
    TestPlanEntryResponse,
    TestPlanSummary,
    ExecutionSessionSummary,
    ExecutionEnvironmentSnapshot,
    ExecutionSessionList,
    TestPlanDetail,
    TestPlanProgress,
    TestPlanHistoryItem,
    UserPlanCreate,
    PlanMetadataUpdate,
    ArchiveRequest,
    EntryBatch,
    EntryUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_is_stale(
    plan: TestPlan, last_activity_at: Optional[datetime], entry_count: int,
) -> bool:
    """Plan-drafting staleness, parallel of ``_compute_is_stale`` for
    execution.  An interrupted drafting looks like a ``draft`` with no
    entries that hasn't seen agent activity for ``_STALE_THRESHOLD_SECONDS``.
    A draft WITH entries is a finished plan waiting for its first run —
    since v2.433.0 nothing moves a plan out of ``draft`` before that — so
    it is never stale.
    """
    if plan.status != TestPlanStatus.DRAFT.value or entry_count > 0:
        return False
    ref = last_activity_at or plan.created_at
    if ref is None:
        return False
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ref).total_seconds() > _STALE_THRESHOLD_SECONDS


def _plan_to_summary(
    plan: TestPlan,
    progress: Dict[str, Any],
    last_activity_at: Optional[datetime] = None,
) -> TestPlanSummary:
    return TestPlanSummary(
        id=plan.id,
        project_id=plan.project_id,
        version=plan.version,
        title=plan.title,
        description=plan.description,
        status=plan.status,
        agent_name=plan.agent.name if plan.agent else None,
        created_by_username=(
            plan.created_by_user.username if plan.created_by_user else None
        ),
        created_by_full_name=(
            (plan.created_by_user.full_name or None) if plan.created_by_user else None
        ),
        entry_count=progress["total_entries"],
        entries_done=progress["hosts_tested"],
        completion_pct=progress["completion_pct"],
        archive_reason=plan.archive_reason,
        generated_by_model=plan.generated_by_model,
        generated_by_tool=plan.generated_by_tool,
        prompt_version=plan.prompt_version,
        source_kind=plan.source_kind or "unspecified",
        source_host_ids=plan.source_host_ids,
        source_plan_id=plan.source_plan_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        completed_at=plan.completed_at,
        last_activity_at=last_activity_at,
        is_stale=_plan_is_stale(plan, last_activity_at, progress["total_entries"]),
        agent_session_id=plan.agent_session_id,
    )


def _entry_to_response(entry: TestPlanEntry) -> TestPlanEntryResponse:
    host = entry.host
    return TestPlanEntryResponse(
        id=entry.id,
        host_id=entry.host_id,
        host_ip=host.ip_address if host else None,
        host_hostname=host.hostname if host else None,
        name_id=entry.name_id,
        target_fqdn=entry.target_name.fqdn if entry.target_name else None,
        priority=entry.priority,
        test_phase=entry.test_phase,
        proposed_tests=entry.proposed_tests or [],
        rationale=entry.rationale,
        status=entry.status,
        findings=entry.findings,
        results_data=entry.results_data,
        notes=entry.notes,
        assigned_to_id=entry.assigned_to_id,
        started_at=entry.started_at,
        completed_at=entry.completed_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=TestPlanSummary, status_code=201, summary="Create a test plan")
def create_test_plan(
    body: UserPlanCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    svc = TestPlanService(db)
    plan = svc.create_plan(
        project_id=project.id,
        agent_id=None,
        title=body.title,
        description=body.description,
        actor_type="user",
        actor_id=current_user.id,
        created_by_user_id=current_user.id,
    )
    return _plan_to_summary(plan, svc.get_progress(plan.id))


# ---------------------------------------------------------------------------
# From a Hosts-page selection (v2.345.0) — a plan, or a draft's new entries,
# whose targets are a FIXED host list the operator picked, not a query.
# ---------------------------------------------------------------------------

class PlanFromHostsRequest(BaseModel):
    """The Hosts bulk bar's "Test plan" action.

    ``host_ids`` is the selection resolved on the client at the moment of the
    click — a fixed list, recorded as ``source_host_ids`` on a new plan.
    A saved query's membership can change; this cannot, which is what makes
    the plan's provenance reviewable later.
    """
    host_ids: List[int] = Field(..., min_length=1, max_length=10_000)
    rationale: str = Field(..., min_length=1, max_length=4096, description=(
        "Why these hosts — becomes every entry's rationale and is appended to a "
        "new plan's description."
    ))
    # New plan (title required) or an existing DRAFT (plan_id).
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=4096)
    plan_id: Optional[int] = Field(None, gt=0)
    priority: TestEntryPriority = TestEntryPriority.MEDIUM
    test_phase: TestPhase = TestPhase.ENUMERATION
    # Human-readable description of the selection ("all 41 hosts matching
    # subnet 10.1.0.0/16, has:weak_tls") kept in the plan description so a
    # reviewer knows how the fixed list was arrived at.
    selection_summary: Optional[str] = Field(None, max_length=1000)
    # Report what WOULD happen without writing anything — the dialog shows
    # these numbers before the operator submits.
    dry_run: bool = False


class PlanFromHostsResponse(BaseModel):
    plan: Optional[TestPlanSummary] = None
    created_plan: bool = False
    requested: int
    # Entries that were (or would be) created.
    added: int
    # Exclusions, so the operator is told rather than left to count.
    already_in_plan: int
    not_in_project: int
    # Selected hosts that already carry an entry in another (non-archived)
    # plan on this project — "already tested or queued elsewhere", worth a
    # look before duplicating the work.
    planned_elsewhere: int
    dry_run: bool


@router.post(
    "/from-hosts",
    response_model=PlanFromHostsResponse,
    summary="Create a plan, or add to a draft, from a fixed host selection",
)
def create_test_plan_from_hosts(
    body: PlanFromHostsRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    svc = TestPlanService(db)
    requested_ids = list(dict.fromkeys(body.host_ids))

    plan: Optional[TestPlan] = None
    if body.plan_id is not None:
        plan = svc.get_plan(body.plan_id, project.id)
        if not plan:
            raise HTTPException(status_code=404, detail="Test plan not found")
        if plan.status != TestPlanStatus.DRAFT.value:
            raise HTTPException(
                status_code=409,
                detail=f"Only a draft plan accepts a host selection; this plan is {plan.status}.",
            )
    elif not body.title:
        raise HTTPException(status_code=422, detail="title is required when creating a new plan")

    in_project = {
        hid for (hid,) in db.query(Host.id)
        .filter(Host.id.in_(requested_ids), Host.project_id == project.id)
        .all()
    }
    eligible = [hid for hid in requested_ids if hid in in_project]
    not_in_project = len(requested_ids) - len(eligible)

    already_in_plan = 0
    if plan is not None and eligible:
        already_in_plan = (
            db.query(func.count(func.distinct(TestPlanEntry.host_id)))
            .filter(TestPlanEntry.test_plan_id == plan.id, TestPlanEntry.host_id.in_(eligible))
            .scalar()
        ) or 0

    planned_elsewhere = 0
    if eligible:
        q = (
            db.query(func.count(func.distinct(TestPlanEntry.host_id)))
            .join(TestPlan, TestPlan.id == TestPlanEntry.test_plan_id)
            .filter(
                TestPlanEntry.host_id.in_(eligible),
                TestPlan.project_id == project.id,
                TestPlan.status.in_(PLANNED_PLAN_STATUSES),
            )
        )
        if plan is not None:
            q = q.filter(TestPlan.id != plan.id)
        planned_elsewhere = q.scalar() or 0

    counts = dict(
        requested=len(requested_ids),
        already_in_plan=already_in_plan,
        not_in_project=not_in_project,
        planned_elsewhere=planned_elsewhere,
    )
    if body.dry_run:
        return PlanFromHostsResponse(
            plan=_plan_to_summary(plan, svc.get_progress(plan.id)) if plan else None,
            created_plan=False,
            added=max(len(eligible) - already_in_plan, 0),
            dry_run=True,
            **counts,
        )
    if not eligible:
        raise HTTPException(status_code=422, detail="None of the selected hosts belong to this project")

    created_plan = False
    if plan is None:
        note = f"Created from a fixed selection of {len(eligible)} host{'' if len(eligible) == 1 else 's'} on the Hosts page"
        if body.selection_summary:
            note += f" ({body.selection_summary})"
        description = (body.description or "").rstrip()
        description = (
            (description + "\n\n" if description else "")
            + note + ".\n\nWhy these hosts: " + body.rationale.strip()
        )
        plan = svc.create_plan(
            project_id=project.id,
            agent_id=None,
            title=body.title,
            description=description[:4096],
            actor_type="user",
            actor_id=current_user.id,
            created_by_user_id=current_user.id,
            source_kind="manual_hosts",
            source_host_ids=eligible,
        )
        created_plan = True

    entries = [
        {
            "host_id": hid,
            "priority": body.priority.value,
            "test_phase": body.test_phase.value,
            # The analyst authors the tests on the plan; an empty list is
            # honest, a placeholder would be noise a reviewer has to delete.
            "proposed_tests": [],
            "rationale": body.rationale.strip(),
            "notes": None,
        }
        for hid in eligible
    ]
    try:
        created = svc.add_entries(plan, entries, "user", current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.refresh(plan)
    return PlanFromHostsResponse(
        plan=_plan_to_summary(plan, svc.get_progress(plan.id)),
        created_plan=created_plan,
        added=len(created),
        dry_run=False,
        **counts,
    )
@router.post(
    "/{plan_id}/entries",
    response_model=List[TestPlanEntryResponse],
    status_code=201,
    summary="Batch-add entries to a test plan",
)
def add_test_plan_entries(
    body: EntryBatch,
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Add up to 500 entries to an existing test plan.

    Available via JWT auth so agents using user credentials can batch-add
    entries without needing a dedicated agent API key.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    entries_data = [e.model_dump() for e in body.entries]
    try:
        created = svc.add_entries(plan, entries_data, "user", current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return [_entry_to_response(e) for e in created]


@router.get("/", response_model=List[TestPlanSummary], summary="List test plans")
def list_test_plans(
    # v2.433.1 — typed, so a retired status (?status=approved) is a 422 that
    # says so, not an empty list that reads as "no plans".
    status: Optional[TestPlanStatus] = Query(None),
    search: Optional[str] = Query(
        None, description="Case-insensitive substring match on plan title.", max_length=200,
    ),
    limit: Optional[int] = Query(
        None, ge=1, le=200,
        description=(
            "Cap result count.  Omitted means no cap.  CommandPalette / "
            "type-ahead callers should pass a small limit to keep latency "
            "constant as the plan list grows."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    svc = TestPlanService(db)
    plans = svc.list_plans(
        project.id, status_filter=status.value if status else None, search=search, limit=limit,
    )
    progress_map = svc.get_progress_batch([p.id for p in plans])
    # Batched plan-generation activity lookup so a stale ``draft`` plan
    # gets the "Possibly interrupted" badge in the list view too — one
    # GROUP BY against the audit log, no N+1.  Filtered to exclude
    # execution calls (they stamp test_plan_id but have an
    # execution_session_id too).
    plan_ids = [p.id for p in plans]
    activity_by_id: Dict[int, datetime] = {}
    if plan_ids:
        activity_by_id = dict(
            db.query(
                AgentApiCall.test_plan_id,
                func.max(AgentApiCall.created_at),
            )
            .filter(
                AgentApiCall.test_plan_id.in_(plan_ids),
                AgentApiCall.execution_session_id.is_(None),
            )
            .group_by(AgentApiCall.test_plan_id)
            .all()
        )
    return [
        _plan_to_summary(
            p,
            progress_map.get(p.id, svc._empty_progress(p.id)),
            activity_by_id.get(p.id),
        )
        for p in plans
    ]


@router.get("/{plan_id}", response_model=TestPlanDetail, summary="Get test plan with entries")
def get_test_plan(
    plan_id: int = Path(..., gt=0),
    entries_skip: int = Query(
        0,
        ge=0,
        description=(
            "Offset into the plan's entries list.  Default 0.  Combined "
            "with entries_limit for server-paginated detail pages "
            "(v2.85.0)."
        ),
    ),
    entries_limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Maximum number of entries to return.  When omitted (the "
            "pre-v2.85.0 default), every entry is returned in one shot "
            "for backward compatibility.  Frontends that need to scale "
            "to thousands of entries should pass a page size (e.g. 50) "
            "and use entries_total to drive a 'load more' affordance."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        # Distinguish "doesn't exist" from "exists in another project".  A
        # plan is permanently scoped to the project it was generated under;
        # viewing from a different project context would otherwise hide it
        # behind a generic 404 with no way for the user to find it.
        #
        # But only reveal the owning project to a caller who can actually
        # reach it (global admin or a member of that project).  Otherwise
        # the hint leaks cross-tenant existence + the owning project id to
        # anyone enumerating plan ids, so fall through to a generic 404.
        other = db.query(TestPlan.project_id).filter(TestPlan.id == plan_id).first()
        if other:
            other_pid = other[0]
            caller_can_see = _user.role == UserRole.ADMIN or (
                db.query(ProjectMembership.id)
                .filter(
                    ProjectMembership.project_id == other_pid,
                    ProjectMembership.user_id == _user.id,
                )
                .first()
                is not None
            )
            if caller_can_see:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Test plan #{plan_id} belongs to a different project "
                        f"(project #{other_pid}). Switch to that project to view it."
                    ),
                )
        raise HTTPException(status_code=404, detail="Test plan not found")

    progress = svc.get_progress(plan.id)
    new_hosts = svc.count_new_hosts_since_plan(plan)
    last_activity_at = _plan_last_activity(db, plan.id)

    # v2.85.0 — eager-load the entries page WITH each entry's host in
    # a single round-trip.  Pre-v2.85.0 the code reached for ``plan.entries``
    # (lazy fetch -> 1 query) and then ``entry.host`` per row (1 query per
    # entry) in ``_entry_to_response``.  On a 200-entry plan that was 201
    # queries; on a thousand-entry execution it didn't finish in time.
    # selectinload(host) batches every entry's host in a single IN(...)
    # lookup, and the .order_by(id) keeps page ordering stable across
    # calls so "load more" doesn't skip or repeat rows.
    entries_total = (
        db.query(func.count(TestPlanEntry.id))
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .scalar()
        or 0
    )
    entries_q = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .options(selectinload(TestPlanEntry.host))
        .order_by(TestPlanEntry.id.asc())
        .offset(entries_skip)
    )
    if entries_limit is not None:
        entries_q = entries_q.limit(entries_limit)
    entries_rows = entries_q.all()

    return TestPlanDetail(
        id=plan.id,
        project_id=plan.project_id,
        version=plan.version,
        title=plan.title,
        description=plan.description,
        status=plan.status,
        agent_name=plan.agent.name if plan.agent else None,
        created_by_username=(
            plan.created_by_user.username if plan.created_by_user else None
        ),
        created_by_full_name=(
            (plan.created_by_user.full_name or None) if plan.created_by_user else None
        ),
        entry_count=progress["total_entries"],
        entries_done=progress["hosts_tested"],
        completion_pct=progress["completion_pct"],
        archive_reason=plan.archive_reason,
        generated_by_model=plan.generated_by_model,
        generated_by_tool=plan.generated_by_tool,
        prompt_version=plan.prompt_version,
        source_kind=plan.source_kind or "unspecified",
        source_host_ids=plan.source_host_ids,
        source_plan_id=plan.source_plan_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        completed_at=plan.completed_at,
        last_activity_at=last_activity_at,
        is_stale=_plan_is_stale(plan, last_activity_at, progress["total_entries"]),
        agent_session_id=plan.agent_session_id,
        entries=[_entry_to_response(e) for e in entries_rows],
        entries_total=entries_total,
        entries_skip=entries_skip if entries_limit is not None else None,
        entries_limit=entries_limit,
        new_hosts_since_creation=new_hosts,
        filter_criteria=plan.filter_criteria,
        latest_execution_session=_latest_session_summary(db, plan.id),
        execution_session_count=(
            db.query(func.count(ExecutionSession.id))
            .filter(ExecutionSession.test_plan_id == plan.id)
            .scalar()
            or 0
        ),
    )


# Inactivity window after which an ``active`` session is treated as
# "looks interrupted".  Computed server-side specifically so it does not
# drift against the operator's browser clock — a future-dated
# ``started_at`` (operator's clock behind the server) was silently
# pushing the threshold crossing minutes off real elapsed time.
_STALE_THRESHOLD_SECONDS = 15 * 60


def _compute_is_stale(
    session: ExecutionSession, last_activity_at: Optional[datetime]
) -> bool:
    if session.status != ExecutionSessionStatus.ACTIVE.value:
        return False
    ref = last_activity_at or session.started_at
    if ref is None:
        return False
    # Postgres DateTime(timezone=True) returns tz-aware values; defend
    # against any legacy tz-naive rows by treating them as UTC.
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ref).total_seconds() > _STALE_THRESHOLD_SECONDS


def _session_summary(
    session: ExecutionSession,
    last_activity_at: Optional[datetime] = None,
) -> ExecutionSessionSummary:
    """Shared row-builder so the latest-summary, the list endpoint, and
    any future surface emit the same shape from the same ORM rows.

    ``last_activity_at`` is supplied by the caller (derived from the
    agent_api_calls audit log) so this helper stays query-free and the
    list endpoint can resolve it in one batched query.
    """
    env = session.environment or {}
    # Full operator-environment snapshot for the detail panel — parity with
    # recon's ReconEnvironmentSnapshot.  Only emit when a probe arrived (raw
    # body or a probed_at timestamp); otherwise leave null so the UI renders
    # the "no probe" affordance instead of empty fields that look like data.
    env_snapshot: Optional[ExecutionEnvironmentSnapshot] = None
    raw_env = env if isinstance(env, dict) else None
    if raw_env or session.environment_probed_at:
        # tools_status may arrive as a list ([{name,status,issue}, ...]) or a
        # dict keyed by tool name; normalize to the canonical list so legacy
        # rows of either shape render without 500ing (mirrors recon).
        raw_status = (raw_env or {}).get("tools_status")
        if isinstance(raw_status, list):
            tools_status_norm = [e for e in raw_status if isinstance(e, dict)]
        elif isinstance(raw_status, dict):
            tools_status_norm = []
            for name, payload in raw_status.items():
                if isinstance(payload, dict):
                    tools_status_norm.append({"name": name, **payload})
                else:
                    tools_status_norm.append({"name": name, "status": str(payload)})
        else:
            tools_status_norm = []
        env_snapshot = ExecutionEnvironmentSnapshot(
            probed_at=session.environment_probed_at,
            probed_from_ip=getattr(session, "environment_probed_from_ip", None),
            os_family=(raw_env or {}).get("os_family"),
            os_release=(raw_env or {}).get("os_release"),
            shell=(raw_env or {}).get("shell"),
            arch=(raw_env or {}).get("arch"),
            python=(raw_env or {}).get("python"),
            notes=(raw_env or {}).get("notes"),
            tools_status=tools_status_norm,
            raw=raw_env,
        )
    return ExecutionSessionSummary(
        id=session.id,
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
        environment_os_family=env.get("os_family"),
        environment_shell=env.get("shell"),
        environment_probed_at=session.environment_probed_at,
        environment=env_snapshot,
        last_activity_at=last_activity_at,
        is_stale=_compute_is_stale(session, last_activity_at),
        agent_session_id=session.agent_session_id,
    )


def _session_last_activity(db: Session, session_id: int) -> Optional[datetime]:
    """Most recent agent_api_calls timestamp for one execution session."""
    return (
        db.query(func.max(AgentApiCall.created_at))
        .filter(AgentApiCall.execution_session_id == session_id)
        .scalar()
    )


def _plan_last_activity(db: Session, plan_id: int) -> Optional[datetime]:
    """Most recent plan-generation agent call timestamp for one plan.

    Filters out execution calls — those also stamp ``test_plan_id``,
    but on a draft plan they wouldn't exist yet, so the filter is
    mostly defensive.  Stays correct if the same plan is later
    executed and an audit-log row lands with an execution_session_id.
    """
    return (
        db.query(func.max(AgentApiCall.created_at))
        .filter(
            AgentApiCall.test_plan_id == plan_id,
            AgentApiCall.execution_session_id.is_(None),
        )
        .scalar()
    )


def _latest_session_summary(db: Session, plan_id: int) -> Optional[ExecutionSessionSummary]:
    """Build the ExecutionSessionSummary for a plan's most-recent session,
    or None when the plan has never been executed.

    Prefers the active session if one exists; otherwise falls back to
    the most recently started session.  A plan can have many sessions
    (multiple users / agents / runs) — see
    ``/test-plans/{id}/execution-sessions`` for the full list.
    """
    session = (
        db.query(ExecutionSession)
        .filter(ExecutionSession.test_plan_id == plan_id)
        .order_by(
            # active sessions float to the top; among the rest, newest first
            (ExecutionSession.status != "active").asc(),
            ExecutionSession.started_at.desc(),
        )
        .first()
    )
    if session is None:
        return None
    return _session_summary(session, _session_last_activity(db, session.id))


@router.post("/{plan_id}/archive", response_model=TestPlanSummary, summary="Abandon (archive) a test plan")
def archive_test_plan(
    body: ArchiveRequest,
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Abandon a plan — move any non-terminal plan to ARCHIVED.

    The recon-abandon analog for test plans: a non-destructive terminal
    state for a plan nobody means to work any more (``DELETE`` is
    destructive).
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    try:
        plan = svc.archive_plan(plan, current_user.id, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _plan_to_summary(plan, svc.get_progress(plan.id))


@router.patch(
    "/{plan_id}",
    response_model=TestPlanSummary,
    summary="Edit test plan metadata (title / description)",
)
def update_test_plan_metadata(
    body: PlanMetadataUpdate,
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Edit a plan's title and/or description.

    Writes one ``TestPlanHistory`` audit row per changed field so the
    existing history view stays accurate.  Archived plans are frozen —
    the endpoint rejects edits on them to keep historical state clean.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.status == "archived":
        raise HTTPException(
            status_code=400,
            detail="Archived plans are read-only",
        )

    changes: List[tuple] = []
    if body.title is not None and body.title != plan.title:
        changes.append(("title", plan.title, body.title))
        plan.title = body.title
    if body.description is not None and body.description != (plan.description or ""):
        changes.append(("description", plan.description or "", body.description))
        plan.description = body.description

    for field, old, new in changes:
        db.add(TestPlanHistory(
            test_plan_id=plan.id,
            entry_id=None,
            actor_type="user",
            actor_id=current_user.id,
            action="updated",
            field_changed=field,
            old_value=str(old) if old is not None else None,
            new_value=str(new) if new is not None else None,
        ))

    db.commit()
    db.refresh(plan)
    return _plan_to_summary(plan, svc.get_progress(plan.id))


@router.patch(
    "/{plan_id}/entries/{entry_id}",
    response_model=TestPlanEntryResponse,
    summary="Update a test plan entry",
)
def update_test_plan_entry(
    body: EntryUpdate,
    plan_id: int = Path(..., gt=0),
    entry_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    entry = svc.get_entry(entry_id, plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    updates = body.model_dump(exclude_none=True, exclude={"expected_updated_at"})
    if not updates:
        return _entry_to_response(entry)

    try:
        entry = svc.update_entry(
            entry, "user", current_user.id, updates,
            expected_updated_at=body.expected_updated_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return _entry_to_response(entry)


# --- Per-entry execution-results read (v2.28.0) ---
#
# The agent records per-test results into ``test_execution_results`` and
# per-host sanity checks into ``host_sanity_checks`` as it works through
# a plan.  TestPlanDetail's entry rows show only the rollup
# ``findings`` string; the underlying per-test rows are written but
# never surfaced in-page.  This endpoint exposes them so the UI can
# render a per-entry "Test results" panel without forcing the user to
# click Generate Report just to see what their agent ran.

class TestExecutionResultRow(BaseModel):
    id: int
    test_index: int
    status: str
    command_run: Optional[str] = None
    # v2.323.0 — the address the command actually hit (execution evidence
    # references the binding; the finding anchors to the named endpoint).
    observed_ip: Optional[str] = None
    raw_output: Optional[str] = None
    findings_summary: Optional[str] = None
    severity: Optional[str] = None
    is_finding: bool = False
    executed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class HostSanityCheckRow(BaseModel):
    id: int
    method: str
    target_ip: Optional[str] = None
    port_checked: Optional[int] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    source_ip: Optional[str] = None
    dns_result: Optional[str] = None
    passed: bool = False
    details: Optional[str] = None
    checked_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class EntryExecutionResultsResponse(BaseModel):
    entry_id: int
    # The session these rows came from.  ``None`` when the plan has
    # never been executed; in that case ``tests`` and ``sanity_checks``
    # are empty lists.
    execution_session_id: Optional[int] = None
    execution_session_status: Optional[str] = None
    tests: List[TestExecutionResultRow] = Field(default_factory=list)
    sanity_checks: List[HostSanityCheckRow] = Field(default_factory=list)


@router.get(
    "/{plan_id}/execution-sessions",
    response_model=ExecutionSessionList,
    summary="List all execution sessions for a test plan (v2.28.0)",
)
def list_test_plan_execution_sessions(
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return every ExecutionSession recorded against this plan.

    A plan can be executed multiple times — different users, different
    agent models, different terminal hosts.  Each ``/execute`` mints a
    new session and pauses the previous active one (see
    ``execute_test_plan``).  This endpoint surfaces all of them so the
    UI can offer a session picker on the Test Results panel and on
    the report dialog.  Ordered active-first, then newest started.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    sessions = (
        db.query(ExecutionSession)
        .filter(ExecutionSession.test_plan_id == plan.id)
        .order_by(
            (ExecutionSession.status != "active").asc(),
            ExecutionSession.started_at.desc(),
        )
        .all()
    )
    # Resolve last-activity for every session in one batched query
    # against the agent_api_calls audit log — no N+1.
    activity: Dict[int, datetime] = {}
    session_ids = [s.id for s in sessions]
    if session_ids:
        rows = (
            db.query(
                AgentApiCall.execution_session_id,
                func.max(AgentApiCall.created_at),
            )
            .filter(AgentApiCall.execution_session_id.in_(session_ids))
            .group_by(AgentApiCall.execution_session_id)
            .all()
        )
        activity = {sid: ts for sid, ts in rows}
    return ExecutionSessionList(
        plan_id=plan.id,
        sessions=[_session_summary(s, activity.get(s.id)) for s in sessions],
        total=len(sessions),
    )


@router.get(
    "/{plan_id}/entries/{entry_id}/execution-results",
    response_model=EntryExecutionResultsResponse,
    summary="Per-entry test execution results + sanity checks (v2.28.0)",
)
def get_entry_execution_results(
    plan_id: int = Path(..., gt=0),
    entry_id: int = Path(..., gt=0),
    session_id: Optional[int] = Query(
        default=None,
        description=(
            "ExecutionSession ID to read from.  Defaults to the most "
            "recent session for this plan when omitted.  Pass an "
            "explicit ID to view results from an earlier run (a plan "
            "can be executed multiple times)."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return per-test results + per-host sanity checks for an entry.

    Sourced from the explicit ``session_id`` when supplied, otherwise
    from the most-recent ``ExecutionSession`` covering this plan
    (active preferred; otherwise newest by ``started_at``).  No
    aggregation — raw row data, ordered by ``test_index`` for the
    tests and ``checked_at`` for the sanity checks.  Used by the
    TestPlanDetail "Test results" panel (v2.28.0).

    A plan with no execution sessions returns the same shape with
    empty lists and ``execution_session_id`` null, so the UI can
    render a stable "no results yet" state without branching on
    presence.  An explicit ``session_id`` for a session that doesn't
    belong to this plan returns 404.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    entry = svc.get_entry(entry_id, plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if session_id is not None:
        session = (
            db.query(ExecutionSession)
            .filter(
                ExecutionSession.id == session_id,
                ExecutionSession.test_plan_id == plan.id,
            )
            .first()
        )
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Execution session #{session_id} not found for this plan. "
                    "Either it never existed or it belongs to a different plan."
                ),
            )
    else:
        session = (
            db.query(ExecutionSession)
            .filter(ExecutionSession.test_plan_id == plan.id)
            .order_by(
                (ExecutionSession.status != "active").asc(),
                ExecutionSession.started_at.desc(),
            )
            .first()
        )
    if session is None:
        return EntryExecutionResultsResponse(entry_id=entry.id)

    tests = (
        db.query(TestExecutionResult)
        .filter(
            TestExecutionResult.execution_session_id == session.id,
            TestExecutionResult.entry_id == entry.id,
        )
        .order_by(TestExecutionResult.test_index)
        .all()
    )
    sanity_checks = (
        db.query(HostSanityCheck)
        .filter(
            HostSanityCheck.execution_session_id == session.id,
            HostSanityCheck.entry_id == entry.id,
        )
        .order_by(HostSanityCheck.checked_at)
        .all()
    )
    return EntryExecutionResultsResponse(
        entry_id=entry.id,
        execution_session_id=session.id,
        execution_session_status=session.status,
        tests=[TestExecutionResultRow.model_validate(t) for t in tests],
        sanity_checks=[HostSanityCheckRow.model_validate(s) for s in sanity_checks],
    )


# --- All entries' results for a session (v3 alpha.2) ---
#
# Per-entry results are read one entry at a time by the v2.28.0
# Test Results panel.  The v3 cross-execution comparison page wants
# every entry's data for ONE session in a single round trip so the
# diff view doesn't N+1 against a plan with 50 entries.

class EntryResultsBundle(BaseModel):
    """One entry's results within an all-entries bundle.  Same fields
    as ``EntryExecutionResultsResponse`` minus the per-call session
    metadata (which lives once at the bundle level)."""
    entry_id: int
    host_id: int
    host_ip: Optional[str] = None
    host_hostname: Optional[str] = None
    entry_status: str
    tests: List[TestExecutionResultRow] = Field(default_factory=list)
    sanity_checks: List[HostSanityCheckRow] = Field(default_factory=list)


class AllEntryResultsResponse(BaseModel):
    plan_id: int
    execution_session_id: int
    execution_session_status: str
    # The agent session that opened this run (links to /agent-sessions/{id}).
    agent_session_id: Optional[int] = None
    # Session attribution surfaced inline so the comparison UI can
    # render the column header ("claude-opus-4-7 · alice · 2h ago")
    # without a separate session-detail fetch.
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    started_by_username: Optional[str] = None
    agent_name: Optional[str] = None
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    entries: List[EntryResultsBundle] = Field(default_factory=list)
    # v2.86.6 — pagination metadata.  ``entries_total`` is always
    # populated; ``entries_skip`` / ``entries_limit`` echo the slice
    # the caller got back and are non-null only when the caller passed
    # ``entries_limit`` (the back-compat default returns every entry
    # and these stay null).
    entries_total: int = 0
    entries_skip: Optional[int] = None
    entries_limit: Optional[int] = None


@router.get(
    "/{plan_id}/execution-sessions/{session_id}/all-entry-results",
    response_model=AllEntryResultsResponse,
    summary="All entries' results for one execution session (v3 alpha.2)",
)
def get_all_entry_results(
    plan_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    entries_skip: int = Query(
        0,
        ge=0,
        description="Offset into the plan's entries list (v2.86.6).",
    ),
    entries_limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Cap on how many entries to return.  Back-compat default "
            "(None) returns every entry — fine on plans with ~hundreds "
            "of entries but expensive on plans with thousands.  Compare "
            "view callers should paginate; pass entries_limit and use "
            "entries_total to drive a 'load more' or paged fetch (v2.86.6)."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return every plan entry's per-test results + sanity checks
    for one ExecutionSession in a single round trip.

    The cross-execution comparison view fetches this twice (once per
    session being compared) and diffs client-side.  Avoids the N+1
    problem the single-entry endpoint creates for a 50-entry plan.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.id == session_id,
            ExecutionSession.test_plan_id == plan.id,
        )
        .first()
    )
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Execution session #{session_id} not found for this plan. "
                "Either it never existed or it belongs to a different plan."
            ),
        )

    # v2.86.6 — entries paginated when caller passes ``entries_limit``.
    # Total is always computed so the response carries enough metadata
    # for the comparison view to drive a "load more" UI.  The downstream
    # tests + sanity-checks queries are keyed by the paginated entry_id
    # set, so they stay proportional to the page size.
    entries_total = (
        db.query(func.count(TestPlanEntry.id))
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .scalar()
        or 0
    )
    entries_q = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .options(selectinload(TestPlanEntry.host))
        .order_by(TestPlanEntry.id.asc())
        .offset(entries_skip)
    )
    if entries_limit is not None:
        entries_q = entries_q.limit(entries_limit)
    entries = entries_q.all()
    entry_ids = [e.id for e in entries]
    tests_by_entry: dict[int, List[TestExecutionResult]] = {}
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

    checks_by_entry: dict[int, List[HostSanityCheck]] = {}
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
        plan_id=plan.id,
        execution_session_id=session.id,
        execution_session_status=session.status,
        agent_session_id=session.agent_session_id,
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
                tests=[TestExecutionResultRow.model_validate(t)
                       for t in tests_by_entry.get(e.id, [])],
                sanity_checks=[HostSanityCheckRow.model_validate(c)
                               for c in checks_by_entry.get(e.id, [])],
            )
            for e in entries
        ],
        entries_total=entries_total,
        entries_skip=entries_skip if entries_limit is not None else None,
        entries_limit=entries_limit,
    )


@router.get(
    "/{plan_id}/progress",
    response_model=TestPlanProgress,
    summary="Get test plan progress summary",
)
def get_test_plan_progress(
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    return svc.get_progress(plan.id)


@router.get(
    "/{plan_id}/history",
    response_model=List[TestPlanHistoryItem],
    summary="Get test plan change history",
)
def get_test_plan_history(
    plan_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    return svc.get_history(plan.id, limit)


class HostTestPlanEntryResponse(BaseModel):
    """A test plan entry enriched with its parent plan metadata."""
    id: int
    test_plan_id: int
    plan_title: str
    plan_status: str
    agent_name: Optional[str] = None
    host_id: int
    priority: str
    test_phase: str
    proposed_tests: List[StoredProposedTestItem]
    rationale: str
    status: str
    findings: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get(
    "/hosts/{host_id}/entries",
    response_model=List[HostTestPlanEntryResponse],
    summary="Get all test plan entries for a host",
)
def get_host_test_plan_entries(
    host_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return a host's test plan entries.

    Entries of an archived plan never surface, nor do entries flipped to
    `rejected` — an explicit "do not test this".  Every other entry is
    planned or done work (PLANNED_PLAN_STATUSES).
    """
    entries = (
        db.query(TestPlanEntry)
        .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
        .join(Host, TestPlanEntry.host_id == Host.id)
        .options(
            joinedload(TestPlanEntry.test_plan).joinedload(TestPlan.agent),
        )
        .filter(
            TestPlanEntry.host_id == host_id,
            TestPlan.project_id == project.id,
            Host.project_id == project.id,
            TestPlan.status.in_(PLANNED_PLAN_STATUSES),
            TestPlanEntry.status != "rejected",
        )
        .order_by(TestPlanEntry.created_at.desc())
        .all()
    )
    results = []
    for entry in entries:
        plan = entry.test_plan
        results.append(HostTestPlanEntryResponse(
            id=entry.id,
            test_plan_id=plan.id,
            plan_title=plan.title,
            plan_status=plan.status,
            agent_name=plan.agent.name if plan.agent else None,
            host_id=entry.host_id,
            priority=entry.priority,
            test_phase=entry.test_phase,
            proposed_tests=entry.proposed_tests or [],
            rationale=entry.rationale,
            status=entry.status,
            findings=entry.findings,
            notes=entry.notes,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        ))
    return results


@router.delete("/{plan_id}", status_code=204, summary="Delete a test plan")
def delete_test_plan(
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Delete a test plan and cascade-delete its entries and history.

    Permission is analyst — same as plan creation and entry edits.  The
    intended use case is purging a failed/empty plan or one where the agent
    went off-topic; the frontend warns when dispositioned
    entries exist so a misclick on a partially-reviewed plan is hard to
    make accidentally.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    svc.delete_plan(plan)


# ---------------------------------------------------------------------------
# Execution Report — downloadable after a session has results
# ---------------------------------------------------------------------------

_EXECUTION_REPORT_MEDIA_TYPES = {
    "json": "application/json",
    "csv": "text/csv",
    "html": "text/html",
}


@router.get(
    "/{plan_id}/execution-report",
    summary="Download an execution report for a test plan session",
    responses={
        200: {
            "description": "Execution report in the requested format. "
            "Includes plan/session metadata, per-host sanity check, "
            "per-test results with command + output + severity, and "
            "a findings summary.",
            "content": {
                "application/json": {},
                "text/csv": {},
                "text/html": {},
            },
        },
        400: {"description": "No execution sessions exist for this plan"},
        404: {"description": "Plan or session not found"},
    },
)
def export_test_plan_execution_report(
    plan_id: int = Path(..., gt=0),
    session_id: Optional[int] = Query(
        default=None,
        description="Execution session ID. Defaults to the most recent session for this plan.",
    ),
    format_type: str = Query(
        default="html",
        pattern="^(json|csv|html)$",
        alias="format",
        description="Output format: json, csv, or html",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Generate and download a test plan execution report.

    Pulls all per-test results, per-host sanity checks, and findings for
    the specified execution session (or the most recent one if omitted)
    and renders them via ``ExportService``.  The plan must belong to the
    current project.
    """
    # Project scoping — make sure the plan actually belongs to this project
    # before handing it to ExportService.  ExportService doesn't re-check.
    plan = (
        db.query(TestPlan)
        .filter(TestPlan.id == plan_id, TestPlan.project_id == project.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    from app.services.export_service import ExportService

    svc = ExportService(db)
    try:
        result = svc.export_test_plan_execution_report(
            plan_id=plan_id,
            session_id=session_id,
            format_type=format_type,
        )
    except ValueError as exc:
        # Missing session / invalid format → 400
        raise HTTPException(status_code=400, detail=str(exc))

    media_type = _EXECUTION_REPORT_MEDIA_TYPES.get(format_type, "application/octet-stream")
    content = result['data']
    # JSON payload comes back as a dict; serialize here so we can ship it
    # with an attachment disposition instead of FastAPI's default rendering.
    if format_type == 'json':
        import json as _json
        content = _json.dumps(content, indent=2, default=str)

    filename = result.get(
        'filename',
        f"test_plan_{plan_id}_execution_report.{format_type}",
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )



# ---------------------------------------------------------------------------
# Agentic recon — MOVED to POST /projects/{id}/scopes/{scope_id}/recon/start.
#
# v2.11.0 removed the ``/test-plans/generate-recon`` endpoint because it
# conflated reconnaissance with test plan generation: it created a
# TestPlan and told the agent to populate it with entries, but recon's
# actual job is to discover hosts (via the ingestion pipeline), not
# to decide what to test.  Test plan generation is now a separate
# workflow the user triggers AFTER recon populates the host database.
#
# See backend/app/api/v1/endpoints/scopes.py for the new /recon/start
# endpoint, agent_api.py for the /agent/recon/* surface, and
# agent_prompt_service.build_recon_ingest_instructions for the new
# prompt builder.
# ---------------------------------------------------------------------------
