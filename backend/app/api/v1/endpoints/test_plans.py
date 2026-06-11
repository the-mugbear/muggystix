"""
Test Plan Endpoints (User-Facing)

Allows analysts and admins to view, approve, reject, and edit test plans
that were created by AI agents (or manually).  Also provides the
"Execute with AI" entry point that mints an execution session + API key
so an agent can drive test execution with per-test human approval.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.session import get_db
from app.db.models import Host
from app.db.models_auth import User, UserRole, APIKey
from app.db.models_project import Project, ProjectMembership
from app.db.models_agent import (
    Agent, AgentSession, AgentSessionWorkflow, TestPlan, TestPlanEntry,
    TestPlanHistory, TestPlanStatus,
    TestEntryPriority, TestPhase, TestEntryStatus,
    ExecutionSession, ExecutionSessionStatus, ExecutionSessionMode,
    TestExecutionResult, HostSanityCheck, AgentApiCall,
)
from app.services.agent_session_service import create_agent_session
from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings as _settings
from app.services.agent_key_ttl import resolve_expires_at
from app.services.test_plan_service import TestPlanService
from app.services.agent_prompt_service import (
    build_plan_generation_instructions,
    build_execution_instructions,
)
from app.schemas.schemas import ProposedTest, ProposedTestItem
# Shared test-plan schemas live in app/schemas/test_plan_schemas.py (CLAUDE.md
# file-size policy).  Single-use response models stay inline below, next to
# the one endpoint that returns them.
from app.schemas.test_plan_schemas import (
    TestPlanEntryResponse,
    TestPlanSummary,
    ApiKeyStatus,
    ExecutionSessionSummary,
    ExecutionEnvironmentSnapshot,
    ExecutionSessionList,
    TestPlanDetail,
    TestPlanProgress,
    TestPlanHistoryItem,
    UserPlanCreate,
    PlanMetadataUpdate,
    RejectRequest,
    ArchiveRequest,
    EntryCreate,
    EntryBatch,
    EntryUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_is_stale(plan: TestPlan, last_activity_at: Optional[datetime]) -> bool:
    """Plan-generation staleness, parallel of ``_compute_is_stale`` for
    execution and ``_recon_is_stale`` for recon.  An interrupted
    plan-generation looks like a ``draft`` plan that hasn't seen agent
    activity for ``_STALE_THRESHOLD_SECONDS``.  Once the plan moves out
    of ``draft`` (the agent submitted entries, or the human approved /
    rejected / archived), the work is no longer the agent's so we don't
    flag stale.
    """
    if plan.status != TestPlanStatus.DRAFT.value:
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
        entry_count=progress["total_entries"],
        completion_pct=progress["completion_pct"],
        approved_by_id=plan.approved_by_id,
        approved_at=plan.approved_at,
        rejected_by_id=plan.rejected_by_id,
        rejected_at=plan.rejected_at,
        rejection_reason=plan.rejection_reason,
        generated_by_model=plan.generated_by_model,
        generated_by_tool=plan.generated_by_tool,
        prompt_version=plan.prompt_version,
        source_kind=plan.source_kind or "unspecified",
        source_recon_session_id=plan.source_recon_session_id,
        source_host_ids=plan.source_host_ids,
        source_plan_id=plan.source_plan_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        completed_at=plan.completed_at,
        last_activity_at=last_activity_at,
        is_stale=_plan_is_stale(plan, last_activity_at),
    )


def _entry_to_response(entry: TestPlanEntry) -> TestPlanEntryResponse:
    host = entry.host
    return TestPlanEntryResponse(
        id=entry.id,
        host_id=entry.host_id,
        host_ip=host.ip_address if host else None,
        host_hostname=host.hostname if host else None,
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
    current_user: User = Depends(require_project_role("analyst")),
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
# Generate with AI — creates plan + provisions agent key
# ---------------------------------------------------------------------------

class FilterCriteria(BaseModel):
    subnets: Optional[str] = None
    ports: Optional[str] = None
    services: Optional[str] = None
    # v2.21.0 — preferred severity filter.  ``min_severity="high"`` matches
    # hosts with at least one vulnerability of severity high *or* critical,
    # which is the mental model most users have.  Replaces the older
    # has_critical_vulns + has_high_vulns checkbox pair below (which AND'd
    # — picking both meant "host with a critical AND a separate high"
    # rather than "any high or above").  The legacy fields are kept for
    # backward compatibility with plans stored before this change.
    min_severity: Optional[Literal["critical", "high", "medium", "low"]] = None
    has_critical_vulns: Optional[bool] = None
    has_high_vulns: Optional[bool] = None
    search: Optional[str] = None


class GeneratePlanRequest(BaseModel):
    title: str = Field(..., max_length=200, min_length=1)
    description: Optional[str] = None
    filter_criteria: Optional[FilterCriteria] = None
    # v3 alpha.3 — typed source-provenance.  Optional: pre-existing
    # clients that don't send these keep working and the plan lands as
    # ``source_kind='unspecified'`` (or ``'filter_set'`` if the API
    # layer infers it from a non-null ``filter_criteria``; see below).
    # Exactly one of the three payload fields should be set when
    # source_kind is supplied — the endpoint validates this and 422s
    # on conflict.
    source_kind: Optional[
        Literal["recon_session", "manual_hosts", "filter_set", "inherited", "unspecified"]
    ] = None
    source_recon_session_id: Optional[int] = Field(None, gt=0)
    source_host_ids: Optional[List[int]] = Field(None, max_length=10_000)
    source_plan_id: Optional[int] = Field(None, gt=0)
    # v2.58.0 — per-plan-key TTL.  Defaults to the deployment's
    # AGENT_KEY_TTL_HOURS; capped at AGENT_KEY_MAX_TTL_HOURS.  Use this
    # when you know the engagement will run longer than a day so the
    # agent doesn't hit a mid-flight expiry.
    ttl_hours: Optional[int] = Field(None, ge=1)


class GeneratePlanResponse(BaseModel):
    plan_id: int
    plan_title: str
    plan_status: str
    agent_id: int
    api_key: str
    instructions: str


@router.post(
    "/generate",
    response_model=GeneratePlanResponse,
    status_code=201,
    summary="Create a test plan and provision an agent key",
)
def generate_test_plan(
    body: GeneratePlanRequest,
    request: Request,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Create a test plan and return an agent API key + instructions.

    The user's project agent is reused if one exists; a fresh API key is
    minted for this plan and **scoped to that plan alone** via the
    ``api_keys.test_plan_id`` column.  Concurrent agents working on
    different plans therefore get fully independent keys — a rotation or
    revocation targeting one plan's key never touches the other.  If no
    agent exists, one is created automatically.  The returned
    instructions block can be copied directly to an AI agent.
    """
    # --- Find or create the user's agent for this project ---
    agent = (
        db.query(Agent)
        .filter(Agent.project_id == project.id, Agent.owner_id == current_user.id)
        .first()
    )

    if agent and not agent.is_active:
        agent.is_active = True

    if not agent:
        agent = Agent(
            name=f"{current_user.username}-agent",
            project_id=project.id,
            owner_id=current_user.id,
            description="Auto-provisioned for test plan generation",
        )
        db.add(agent)
        db.flush()

    # --- Create the test plan FIRST so we can scope the key to it ---
    # create_plan runs its insert inside a SAVEPOINT and does not commit
    # (commit_after=False), so a version-collision retry cannot roll back
    # the api_key row we add immediately below.  Both rows commit together.
    fc = body.filter_criteria.model_dump(exclude_none=True) if body.filter_criteria else None

    # v3 alpha.3 — source-provenance validation.  The four payload columns
    # are mutually exclusive; the endpoint enforces it (the DB does not).
    # Inference rule: if the client omits ``source_kind`` but supplied
    # filter_criteria, treat it as ``filter_set`` so existing UI flows get
    # provenance without changing their request body.  If everything is
    # omitted, fall through to ``unspecified`` (the column default).
    source_kind = body.source_kind
    if source_kind is None and fc:
        source_kind = "filter_set"

    if source_kind == "recon_session":
        if body.source_recon_session_id is None:
            raise HTTPException(
                status_code=422,
                detail="source_kind='recon_session' requires source_recon_session_id",
            )
        if (
            body.source_host_ids is not None
            or body.source_plan_id is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="source_kind='recon_session' is mutually exclusive with other source_* payloads",
            )
    elif source_kind == "manual_hosts":
        if not body.source_host_ids:
            raise HTTPException(
                status_code=422,
                detail="source_kind='manual_hosts' requires a non-empty source_host_ids list",
            )
        if (
            body.source_recon_session_id is not None
            or body.source_plan_id is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="source_kind='manual_hosts' is mutually exclusive with other source_* payloads",
            )
    elif source_kind == "inherited":
        if body.source_plan_id is None:
            raise HTTPException(
                status_code=422,
                detail="source_kind='inherited' requires source_plan_id",
            )
        if (
            body.source_recon_session_id is not None
            or body.source_host_ids is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="source_kind='inherited' is mutually exclusive with other source_* payloads",
            )
    # filter_set + unspecified: any further payload fields are silently
    # ignored — the contract says the payload is only meaningful for
    # the three kinds above.

    svc = TestPlanService(db)
    plan = svc.create_plan(
        project_id=project.id,
        agent_id=agent.id,
        title=body.title,
        description=body.description,
        actor_type="user",
        actor_id=current_user.id,
        created_by_user_id=current_user.id,
        filter_criteria=fc if fc else None,
        commit_after=False,
        source_kind=source_kind,
        source_recon_session_id=(
            body.source_recon_session_id
            if source_kind == "recon_session" else None
        ),
        source_host_ids=(
            body.source_host_ids if source_kind == "manual_hosts" else None
        ),
        source_plan_id=(
            body.source_plan_id if source_kind == "inherited" else None
        ),
    )

    # --- Mint a per-plan API key (linked to a unified plan_generation
    # AgentSession base — R5 expand-completion) ---
    base_session = create_agent_session(
        db,
        workflow=AgentSessionWorkflow.PLAN_GENERATION.value,
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=current_user.id,
        plan_id=plan.id,
    )
    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key_obj = APIKey(
        agent_id=agent.id,
        test_plan_id=plan.id,
        agent_session_id=base_session.id,
        name=f"plan-{plan.id}",
        key_hash=key_hash,
        key_prefix=raw_key[:14],
        expires_at=resolve_expires_at(body.ttl_hours),
    )
    db.add(api_key_obj)

    instructions = build_plan_generation_instructions(
        request=request,
        plan_id=plan.id,
        plan_title=plan.title,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        filter_criteria=fc if fc else None,
    )

    db.commit()
    db.refresh(plan)

    # NOTE: raw_key is returned once for user display and must never appear
    # in server logs.  Do not add response-body logging middleware without
    # redacting the api_key field from this endpoint's output.
    return GeneratePlanResponse(
        plan_id=plan.id,
        plan_title=plan.title,
        plan_status=plan.status,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
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
    current_user: User = Depends(require_project_role("analyst")),
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
    status: Optional[str] = Query(None),
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
    plans = svc.list_plans(project.id, status_filter=status, search=search, limit=limit)
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

    # Build the API-key status from the most-recent plan-bound key.
    # has_key=False on manual plans (agent_id null, no key minted).
    api_key_status = ApiKeyStatus()
    key_row = (
        db.query(APIKey)
        .filter(APIKey.test_plan_id == plan.id)
        .order_by(APIKey.created_at.desc())
        .first()
    )
    if key_row is not None:
        # Some Postgres drivers return a tz-naive datetime even for a
        # DateTime(timezone=True) column; normalise before arithmetic.
        expires_at = key_row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_in = (
            int((expires_at - datetime.now(timezone.utc)).total_seconds())
            if expires_at is not None
            else None
        )
        api_key_status = ApiKeyStatus(
            has_key=True,
            is_active=bool(
                key_row.is_active and expires_in is not None and expires_in > 0
            ),
            expires_at=expires_at,
            expires_in_seconds=expires_in,
            key_prefix=key_row.key_prefix,
        )

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
        entry_count=progress["total_entries"],
        completion_pct=progress["completion_pct"],
        approved_by_id=plan.approved_by_id,
        approved_at=plan.approved_at,
        rejected_by_id=plan.rejected_by_id,
        rejected_at=plan.rejected_at,
        rejection_reason=plan.rejection_reason,
        generated_by_model=plan.generated_by_model,
        generated_by_tool=plan.generated_by_tool,
        prompt_version=plan.prompt_version,
        source_kind=plan.source_kind or "unspecified",
        source_recon_session_id=plan.source_recon_session_id,
        source_host_ids=plan.source_host_ids,
        source_plan_id=plan.source_plan_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        completed_at=plan.completed_at,
        last_activity_at=last_activity_at,
        is_stale=_plan_is_stale(plan, last_activity_at),
        entries=[_entry_to_response(e) for e in entries_rows],
        entries_total=entries_total,
        entries_skip=entries_skip if entries_limit is not None else None,
        entries_limit=entries_limit,
        new_hosts_since_creation=new_hosts,
        filter_criteria=plan.filter_criteria,
        api_key=api_key_status,
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


@router.post("/{plan_id}/approve", response_model=TestPlanSummary, summary="Approve a test plan")
def approve_test_plan(
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    try:
        plan = svc.approve_plan(plan, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _plan_to_summary(plan, svc.get_progress(plan.id))


@router.post("/{plan_id}/reject", response_model=TestPlanSummary, summary="Reject a test plan")
def reject_test_plan(
    body: RejectRequest,
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    try:
        plan = svc.reject_plan(plan, current_user.id, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _plan_to_summary(plan, svc.get_progress(plan.id))


@router.post("/{plan_id}/archive", response_model=TestPlanSummary, summary="Abandon (archive) a test plan")
def archive_test_plan(
    body: ArchiveRequest,
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Abandon a plan — move any non-terminal plan to ARCHIVED.

    The recon-abandon analog for test plans: a non-destructive terminal
    state for approved/in-progress plans that are no longer relevant
    (``reject`` only applies pre-approval; ``DELETE`` is destructive).
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


class RotateKeyResponse(BaseModel):
    plan_id: int
    api_key: str = Field(..., description='Plaintext key — shown ONCE, paste into your agent session.')
    expires_at: datetime


@router.post(
    "/{plan_id}/rotate-key",
    response_model=RotateKeyResponse,
    status_code=201,
    summary="Mint a fresh agent API key for an existing test plan",
)
def rotate_test_plan_key(
    plan_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Issue a new plaintext API key for ``plan_id`` and revoke prior keys.

    Use when the original /generate key has expired (default 24h TTL) but
    the user wants to continue agent work on the same plan rather than
    starting a fresh plan from scratch.  Existing entries are untouched —
    only the auth token changes.  The key is bound to the plan's agent
    and the plan itself (``api_keys.test_plan_id``), same scoping as the
    original /generate flow.

    Returns the plaintext key exactly once; the server stores only the
    sha256 hash.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if not plan.agent_id:
        raise HTTPException(
            status_code=400,
            detail="This plan has no associated agent; rotating a key would create an unbound credential.",
        )

    # Revoke-then-mint through the shared helper so a concurrent rotate (or
    # rotate racing /execute) translates the uq_api_key_plan_active conflict
    # to 409 instead of a bare 500.  A failed rotation leaves the old key
    # intact (transaction rolls back); a successful one leaves exactly one
    # active key (the new one).  /rotate-key uses the deployment-default TTL;
    # for a non-default TTL, callers should use /renew-key instead.
    raw_key = _mint_plan_agent_key(
        db, agent=plan.agent, plan=plan, name=f"plan-{plan.id}",
    )
    new_key = (
        db.query(APIKey)
        .filter(APIKey.test_plan_id == plan.id, APIKey.is_active.is_(True))
        .order_by(APIKey.id.desc())
        .first()
    )
    db.commit()
    db.refresh(new_key)

    logger.info(
        "Plan %d agent key rotated by user %s — new key expires %s",
        plan.id, current_user.username, new_key.expires_at,
    )

    return RotateKeyResponse(
        plan_id=plan.id,
        api_key=raw_key,
        expires_at=new_key.expires_at,
    )


@router.post(
    "/{plan_id}/resume-generation",
    response_model=GeneratePlanResponse,
    status_code=201,
    summary="Resume an interrupted plan-generation session (v2.48.7)",
)
def resume_plan_generation(
    plan_id: int = Path(..., gt=0),
    request: Request = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Re-mint a fresh agent key and rebuild the plan-generation
    instructions block for a draft plan whose agent went quiet.

    Mirrors the execution and recon resume endpoints — the third leg of
    the agentic-workflow Resume affordance.  Valid only for a plan in
    ``draft`` status (the state in which the agent is meant to be
    populating entries); any other status is the human's turn or
    execution territory and returns 409.

    Existing entries are preserved; the resumed agent continues via
    ``GET /agent/test-plans/{plan_id}/context`` with the
    ``not_in_plan_id`` cursor (see AGENTS.md § Resuming plan
    creation).  Minting through ``_mint_plan_agent_key`` revokes any
    prior active key for the plan — load-bearing so the dead agent's
    key can't be used to interleave writes.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.status != TestPlanStatus.DRAFT.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot resume plan generation while the plan is in "
                f"'{plan.status}' status — resume applies only to a "
                "draft plan where the agent is supposed to be "
                "populating entries."
            ),
        )

    # Reuse the plan's original agent; fall back to find/create only
    # if that row is gone.
    agent = _resolve_execution_agent(
        db, project=project, user=current_user, prefer_agent_id=plan.agent_id
    )
    plan.agent_id = agent.id
    raw_key = _mint_plan_agent_key(
        db, agent=agent, plan=plan, name_suffix="-resume-gen"
    )

    instructions = build_plan_generation_instructions(
        request=request,
        plan_id=plan.id,
        plan_title=plan.title,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        filter_criteria=plan.filter_criteria,
    )

    db.commit()
    db.refresh(plan)

    return GeneratePlanResponse(
        plan_id=plan.id,
        plan_title=plan.title,
        plan_status=plan.status,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
    )


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
    current_user: User = Depends(require_project_role("analyst")),
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
    current_user: User = Depends(require_project_role("analyst")),
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
    proposed_tests: List[ProposedTestItem]
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
    """Return test plan entries for a host from accepted plans.

    Workflow context (the "approve first, triage during execution" model):

    Once a human has approved the parent plan as a whole, every entry in
    that plan is implicitly part of the agreed work — `proposed` no
    longer means "untriaged", it means "queued for execution".  Testers
    then walk entries through `proposed → in_progress → completed`, or
    flip them to `rejected` if they decide on closer inspection that an
    entry isn't worth running.

    Two filters apply:

    1. **Plan-level**: only `approved` / `in_progress` / `completed`
       plans surface.  Draft/proposed/rejected/archived plans never
       leak entries onto host pages.

    2. **Entry-level**: exclude `rejected` entries.  A reviewer (or
       tester) flipping an entry to `rejected` is an explicit "do not
       test this", and the host page should respect that.  All other
       statuses (`proposed`, `approved`, `in_progress`, `completed`)
       surface, since they all represent work the team has agreed to
       or done.
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
            TestPlan.status.in_(("approved", "in_progress", "completed")),
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
    _user: User = Depends(require_project_role("analyst")),
):
    """Delete a test plan and cascade-delete its entries and history.

    Permission is analyst — same as plan creation, approval, and entry
    edits.  The intended use case is purging a failed/empty plan or one
    where the agent went off-topic; the frontend warns when dispositioned
    entries exist so a misclick on a partially-reviewed plan is hard to
    make accidentally.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    svc.delete_plan(plan)


# ---------------------------------------------------------------------------
# Execute with AI — creates an execution session + provisions agent key
# ---------------------------------------------------------------------------

class ExecuteResponse(BaseModel):
    execution_session_id: int
    plan_id: int
    plan_title: str
    agent_id: int
    api_key: str
    instructions: str


# ---------------------------------------------------------------------------
# Execution-session lifecycle helpers — shared by execute + resume so the
# agent-resolution and key-minting plumbing lives in one place.
# ---------------------------------------------------------------------------

def _resolve_execution_agent(
    db: Session,
    *,
    project: Project,
    user: User,
    prefer_agent_id: Optional[int] = None,
) -> Agent:
    """Resolve the agent that owns an execution session.

    Prefers ``prefer_agent_id`` (the session's original agent, on resume),
    then the user's existing project agent, auto-provisioning one if
    neither exists.  Reactivates a deactivated agent.
    """
    agent: Optional[Agent] = None
    if prefer_agent_id is not None:
        agent = db.query(Agent).filter(Agent.id == prefer_agent_id).first()
    if agent is None:
        agent = (
            db.query(Agent)
            .filter(Agent.project_id == project.id, Agent.owner_id == user.id)
            .first()
        )
    if agent is not None:
        if not agent.is_active:
            agent.is_active = True
        return agent
    agent = Agent(
        name=f"{user.username}-agent",
        project_id=project.id,
        owner_id=user.id,
        description="Auto-provisioned for test plan execution",
    )
    db.add(agent)
    db.flush()
    return agent


def _ensure_plan_agent_session(db: Session, *, agent: Agent, plan: TestPlan) -> int:
    """Return the id of an AgentSession for this plan, reusing the most recent
    one if present, else creating a plan_generation base.

    Used by the key-mint helper for callers (rotate-key / resume-generation /
    resume) that re-mint a plan key without owning a fresh session — so the
    key still links to the plan's unified session (R5).
    """
    existing = (
        db.query(AgentSession)
        .filter(AgentSession.plan_id == plan.id)
        .order_by(AgentSession.id.desc())
        .first()
    )
    if existing is not None:
        return existing.id
    base = create_agent_session(
        db,
        workflow=AgentSessionWorkflow.PLAN_GENERATION.value,
        project_id=plan.project_id,
        agent_id=agent.id,
        started_by_id=None,
        plan_id=plan.id,
    )
    return base.id


def _mint_plan_agent_key(
    db: Session,
    *,
    agent: Agent,
    plan: TestPlan,
    name_suffix: str = "",
    name: Optional[str] = None,
    agent_session_id: Optional[int] = None,
) -> str:
    """Mint a fresh per-plan agent API key; return the plaintext key.

    Revokes every prior active key for the plan first, so at most one
    agent key is ever live per plan.  This is load-bearing for safety:
    agent execution endpoints resolve work by ``(test_plan_id,
    status=active)`` rather than by key, so if ``/execute`` or
    ``/resume`` left the previous key usable, two live keys would let
    two agents write into the same execution session.  Mirrors the
    revoke-then-mint pattern of the key-regenerate endpoint.

    Flushes the revoke + insert here and translates a partial-unique-index
    violation (``uq_api_key_plan_active``) into 409 so every caller —
    /execute, /resume, /rotate-key, /resume-generation — gets the same
    concurrency semantics instead of a bare 500 when two key-minting
    requests race on a backend where the plan row lock is a no-op (SQLite)
    or where no lock is taken (rotate).
    """
    db.query(APIKey).filter(
        APIKey.test_plan_id == plan.id,
        APIKey.is_active.is_(True),
    ).update({"is_active": False}, synchronize_session=False)

    # Link the key to the plan's unified AgentSession; callers that own a
    # fresh session pass it explicitly, others reuse/create via the helper (R5).
    if agent_session_id is None:
        agent_session_id = _ensure_plan_agent_session(db, agent=agent, plan=plan)

    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    db.add(
        APIKey(
            agent_id=agent.id,
            test_plan_id=plan.id,
            agent_session_id=agent_session_id,
            name=name or f"exec-plan-{plan.id}{name_suffix}",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=raw_key[:14],
            expires_at=resolve_expires_at(None),
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Another agent key became active for this plan "
                "concurrently. Refresh and retry."
            ),
        ) from exc
    return raw_key


@router.post(
    "/{plan_id}/execute",
    response_model=ExecuteResponse,
    status_code=201,
    summary="Start a test execution session with an AI agent",
)
def execute_test_plan(
    plan_id: int = Path(..., gt=0),
    request: Request = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Create an execution session and return an agent API key + instructions.

    Mirrors the `/generate` endpoint pattern: provisions an agent,
    mints a time-limited API key, and builds an instructions block
    the user copies to their AI agent.  The instructions guide the
    agent through the safety protocol: per-host sanity check, per-test
    human approval, and result recording.

    The plan must be in `approved` or `in_progress` status.  If the
    plan is `approved`, this transitions it to `in_progress`.  At most
    one execution session per plan may be `active` at a time — creating
    a new one pauses the previous.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    # v2.91.3 (code review #2) — serialize concurrent /execute calls
    # against the same plan by locking the plan row.  Pre-fix two
    # double-clicked /execute requests could both pass the
    # one-active-session check, both mint live keys, and both insert
    # ACTIVE execution_sessions rows; the agent's /execution-context
    # resolution then picked an arbitrary one of the two and the
    # audit trail split between them.  The plan row is the natural
    # serialization point (every per-plan write derives from it),
    # and Postgres FOR UPDATE blocks until the prior tx commits.
    # SQLite (tests) treats with_for_update as a no-op — the partial-
    # unique index added in the same revision is the SQLite backstop.
    db.query(TestPlan).filter(TestPlan.id == plan.id).with_for_update().first()

    if plan.status not in ("approved", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot execute a plan in {plan.status} status — approve it first.",
        )

    entry_count = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .count()
    )
    if entry_count == 0:
        raise HTTPException(status_code=400, detail="Cannot execute an empty test plan.")

    # Resolve the agent and mint a fresh per-plan key.  Minting revokes
    # any prior active key for the plan (see _mint_plan_agent_key), so
    # only one agent key is ever live per plan.
    agent = _resolve_execution_agent(db, project=project, user=current_user)
    # Unified execution base session, linked to both the key and the
    # ExecutionSession detail row below (R5 expand-completion).
    base_session = create_agent_session(
        db,
        workflow=AgentSessionWorkflow.EXECUTION.value,
        project_id=plan.project_id,
        agent_id=agent.id,
        started_by_id=current_user.id,
        plan_id=plan.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    )
    raw_key = _mint_plan_agent_key(db, agent=agent, plan=plan, agent_session_id=base_session.id)

    # --- Pause any existing active session for this plan ---
    db.query(ExecutionSession).filter(
        ExecutionSession.test_plan_id == plan.id,
        ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
    ).update(
        {"status": ExecutionSessionStatus.PAUSED.value},
        synchronize_session=False,
    )
    # Flush so the partial-unique index sees the PAUSED state before
    # the new ACTIVE row is inserted; without this the constraint
    # would reject inside the same transaction.
    db.flush()

    # --- Create the execution session ---
    session = ExecutionSession(
        test_plan_id=plan.id,
        agent_id=agent.id,
        started_by_id=current_user.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        agent_session_id=base_session.id,
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError as exc:
        # The partial-unique index on (test_plan_id WHERE status='active')
        # rejected the insert — another /execute or /resume committed
        # an ACTIVE session for this plan between our row lock attempt
        # and the insert (only possible on backends where the row lock
        # is a no-op, e.g. SQLite).  Translate to 409 so the operator
        # retries against the now-existing session.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Another execution session became active for this plan "
                "concurrently. Refresh and resume the existing session "
                "instead of starting a new one."
            ),
        ) from exc

    # Transition plan to in_progress if it was just approved.
    if plan.status == "approved":
        plan.status = "in_progress"

    instructions = build_execution_instructions(
        request=request,
        plan_id=plan.id,
        plan_title=plan.title,
        session_id=session.id,
        entry_count=entry_count,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        project_slug=project.slug,
    )

    db.commit()

    return ExecuteResponse(
        execution_session_id=session.id,
        plan_id=plan.id,
        plan_title=plan.title,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
    )


@router.post(
    "/{plan_id}/execution-sessions/{session_id}/resume",
    response_model=ExecuteResponse,
    status_code=201,
    summary="Resume an interrupted execution session (v2.47.0)",
)
def resume_execution_session(
    plan_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    request: Request = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Re-mint an agent API key for an existing, non-terminal execution session.

    When an operator's host crashes mid-execution the session row stays
    ``active`` but the agent process — and its API key — are gone.  The
    only prior path forward was ``/execute``, which *creates a new
    session*.  This endpoint resumes the SAME session: every per-test
    result (``TestExecutionResult``) and per-host sanity check
    (``HostSanityCheck``) is preserved, so the agent continues from where
    it stopped via ``/agent/test-plans/{plan_id}/execution-context``
    rather than starting fresh.

    Valid only for a session in ``active`` or ``paused`` status — calling
    it on a terminal session (``completed`` / ``failed`` / ``abandoned``)
    returns 409.  Honors the one-active-session-per-plan invariant: any
    *other* active session for the plan is paused.  A resume checkpoint
    is appended to the session notes for the human-review trail.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, project.id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")

    # v2.91.3 (code review #2) — same plan-row lock as /execute.
    # Resume + execute compete for the same one-active-session
    # invariant; the lock serializes them so a concurrent /execute
    # can't slip an ACTIVE row in between this resume's pause-others
    # update and the session.status flip.
    db.query(TestPlan).filter(TestPlan.id == plan.id).with_for_update().first()

    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.id == session_id,
            ExecutionSession.test_plan_id == plan.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=404, detail="Execution session not found for this plan"
        )

    resumable = {
        ExecutionSessionStatus.ACTIVE.value,
        ExecutionSessionStatus.PAUSED.value,
    }
    if session.status not in resumable:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot resume a session in '{session.status}' status — "
                "resume applies only to an interrupted active/paused session."
            ),
        )
    if plan.status not in ("approved", "in_progress"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot resume execution while the plan is in "
                f"'{plan.status}' status."
            ),
        )

    entry_count = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .count()
    )

    # Reuse the session's original agent and mint a fresh per-plan key.
    # Minting revokes the prior (now-orphaned) key — load-bearing: without
    # it the crashed agent's key would stay usable and a second agent
    # could write into this same resumed session.
    agent = _resolve_execution_agent(
        db, project=project, user=current_user, prefer_agent_id=session.agent_id
    )
    session.agent_id = agent.id
    raw_key = _mint_plan_agent_key(db, agent=agent, plan=plan, name_suffix="-resume")

    # One-active-session-per-plan: pause any OTHER active session.
    db.query(ExecutionSession).filter(
        ExecutionSession.test_plan_id == plan.id,
        ExecutionSession.id != session.id,
        ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
    ).update(
        {"status": ExecutionSessionStatus.PAUSED.value},
        synchronize_session=False,
    )
    # Flush the pause-others UPDATE before flipping this session back
    # to ACTIVE so the partial-unique index sees them sequentially.
    db.flush()

    session.status = ExecutionSessionStatus.ACTIVE.value
    if plan.status == "approved":
        plan.status = "in_progress"
    try:
        db.flush()
    except IntegrityError as exc:
        # Same defense-in-depth as /execute: catch the partial-unique
        # rejection if the plan row lock was a no-op (SQLite).
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Another execution session became active for this plan "
                "concurrently. Refresh the plan and try again."
            ),
        ) from exc

    # Checkpoint note for the human-review trail — capped at 8 KiB,
    # most-recent-kept, consistent with the session-complete notes cap.
    resume_note = (
        f"[{datetime.now(timezone.utc).isoformat()}] Session resumed by "
        f"{current_user.full_name or current_user.username} "
        f"— fresh API key minted (prior key revoked); prior progress preserved."
    )
    session.notes = (
        f"{session.notes}\n{resume_note}" if session.notes else resume_note
    )[-8192:]

    instructions = build_execution_instructions(
        request=request,
        plan_id=plan.id,
        plan_title=plan.title,
        session_id=session.id,
        entry_count=entry_count,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        resumed=True,
        project_slug=project.slug,
    )

    db.commit()

    return ExecuteResponse(
        execution_session_id=session.id,
        plan_id=plan.id,
        plan_title=plan.title,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
    )


# ---------------------------------------------------------------------------
# Execution Report — downloadable after a session has results
# ---------------------------------------------------------------------------

_EXECUTION_REPORT_MEDIA_TYPES = {
    "json": "application/json",
    "csv": "text/csv",
    "html": "text/html",
    "pdf": "application/pdf",
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
                "application/pdf": {},
            },
        },
        400: {"description": "No execution sessions exist for this plan"},
        404: {"description": "Plan or session not found"},
        501: {"description": "PDF export unavailable (WeasyPrint missing)"},
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
        pattern="^(json|csv|html|pdf)$",
        alias="format",
        description="Output format: json, csv, html, or pdf",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role("analyst")),
):
    """Generate and download a test plan execution report.

    Pulls all per-test results, per-host sanity checks, and findings for
    the specified execution session (or the most recent one if omitted)
    and renders them via ``ExportService``.  The plan must belong to the
    current project.  PDF rendering requires WeasyPrint on the backend
    image; if unavailable the endpoint returns ``501`` with a diagnostic
    message instead of crashing.
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
    except RuntimeError as exc:
        # WeasyPrint not installed → 501 so the frontend can surface the
        # install instructions without the user seeing a stack trace.
        raise HTTPException(status_code=501, detail=str(exc))

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
