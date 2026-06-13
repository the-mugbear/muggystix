"""
Agent API — test execution endpoints.

Agent records results as it works through an approved plan with
per-test human approval.  Split out of agent_api.py.
"""

import logging
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models_agent import (
    Agent, TestPlan, TestPlanEntry,
    ExecutionSession, ExecutionSessionStatus,
    TestExecutionResult, TestExecutionStatus,
    HostSanityCheck,
)
from app.core.config import settings as _settings
from app.core.security import log_audit_event
from app.api.deps import require_plan_scope, check_agent_rate_limit
from app.services.test_plan_service import TestPlanService
from app.services.notification_service import NotificationService
from app.services.agent_prompt_history import PROMPT_VERSION
from app.services.agent_environment_probe_service import apply_environment_probe

from app.api.v1.endpoints.agent_schemas import (
    ExecutionHostContext, ExecutionContextResponse,
    SanityCheckRequest, TestResultRequest, CompleteEntryRequest,
    ExecutionProgressResponse, PlanResponse,
    EnvironmentProbeRequest, EnvironmentProbeResponse, EnvironmentSummary,
    ExecutionSessionCompleteRequest, ExecutionSessionCompleteResponse,
)
from app.api.v1.endpoints.agent_common import _plan_response

logger = logging.getLogger(__name__)

router = APIRouter()


def _truncate_to_byte_cap(text: str, cap: int) -> str:
    """Truncate ``text`` so its UTF-8 byte length is at most ``cap``.

    Earlier code measured in encoded bytes but sliced by character count,
    so a multi-byte string could still write past the cap after re-encoding
    (e.g. a 3-byte-char-heavy string of 60k characters but 180k bytes).
    Slice the encoded form, decode with ``errors="ignore"`` to drop any
    mid-character break, and reserve byte budget for the trailing marker.
    """
    if not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= cap:
        return text
    marker = "\n\n--- OUTPUT TRUNCATED ---"
    budget = cap - len(marker.encode("utf-8"))
    return encoded[: max(budget, 0)].decode("utf-8", errors="ignore") + marker


# Plan statuses an agent may still write execution data to.  Mirrors the
# read-side guard in get_execution_context.  Without this, the write
# endpoints (sanity check / result / entry completion) would keep
# accepting data onto a plan the operator archived or rejected mid-run,
# as long as a stale ACTIVE ExecutionSession and an in-TTL per-plan key
# still existed — the read path blocked but the writes didn't.
_EXECUTABLE_PLAN_STATUSES = ("approved", "in_progress")


def _require_executable_plan(plan) -> None:
    if plan.status not in _EXECUTABLE_PLAN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan is in {plan.status} status — execution writes are "
                "only allowed while it is approved or in_progress."
            ),
        )


@router.post(
    "/test-plans/{plan_id}/submit",
    response_model=PlanResponse,
    summary="Submit a draft plan for approval",
)
def submit_test_plan(
    plan_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not your test plan")

    if not plan.description:
        raise HTTPException(
            status_code=400,
            detail="Plan description is required before submission. "
            "PATCH /agent/test-plans/{plan_id} with a description summarizing "
            "scope, prioritization, and methodology.",
        )

    try:
        plan = svc.submit_plan(plan, "agent", agent.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Nudge the human approvers — submission is the one human gate in the agent
    # loop. Best-effort: a notification failure must never fail the submission.
    try:
        NotificationService(db).notify_plan_proposed(plan, agent.project_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to notify approvers for proposed plan %s", plan.id, exc_info=True)

    return _plan_response(plan, db)


# --- Environment probe (v2.23.0) ---
#
# MUST be the agent's first call after starting an execution session.
# The probe result is per-session (and via Agent.owner_id, per-user); it
# is echoed back on subsequent /execution-context responses so the
# agent's prompt-render reflects what's actually on the operator's
# host instead of any assumed POSIX defaults.  See AGENTS.md §
# Environment probe.

@router.post(
    "/execution-sessions/{session_id}/environment",
    response_model=EnvironmentProbeResponse,
    summary="Record this execution session's operator environment",
)
def record_execution_environment(
    body: EnvironmentProbeRequest,
    request: Request,
    session_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Record the agent's environment probe for an execution session.

    Called once at session start so subsequent ``/execution-context``
    responses echo back the same data — the agent then translates each
    plan entry's intent into commands shaped to *this* host's tooling
    rather than to any platform-wide assumption.

    Re-POSTing is allowed: a long-running session that survives an OS
    upgrade or a tool install can refresh the probe.  The newest write
    wins (the audit timestamp + ip make stale data legible).
    """
    session = (
        db.query(ExecutionSession)
        .filter(ExecutionSession.id == session_id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Execution session not found")

    # The URL is keyed by session_id, not plan_id, so require_plan_scope
    # can't gate this directly.  Enforce the same audit chain here:
    # (session → plan → agent → owner_id) must match the calling agent
    # AND the API key's plan-scope (if it is plan-scoped) must match
    # the session's plan.  Unscoped keys are allowed if they belong to
    # the same agent.  v2.25.0 — also reject recon-scoped keys: they
    # have scoped_plan_id=None but scoped_scope_id set, which would
    # otherwise pass the "plan_id is None → skip plan check" branch
    # and let a recon key write into an execution session it should
    # have nothing to do with.
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    scoped_assist = getattr(request.state, "scoped_assist_session_id", None)
    if scoped_scope is not None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a reconnaissance run and cannot "
                "write to execution-session endpoints. Use the plan-scoped "
                "key minted by /execute, or the unscoped agent key."
            ),
        )
    if scoped_assist is not None:
        # The completion route at the bottom of this file rejects assist
        # keys too; mirror the guard here so the environment write surface
        # can't drift back into accepting them.  Assist keys bind to the
        # same project agent that owns plans, so plan.agent_id == agent.id
        # and scoped_plan_id is None — both downstream checks would
        # otherwise pass.
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a read-only assist session and "
                "cannot write to execution-session endpoints."
            ),
        )
    plan = session.test_plan
    if not plan or plan.agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not your execution session")
    scoped_plan = getattr(request.state, "scoped_plan_id", None)
    if scoped_plan is not None and scoped_plan != plan.id:
        raise HTTPException(
            status_code=403,
            detail="This API key is scoped to a different test plan.",
        )

    # v2.43.3 (AUD-C1 + AUD-O3): the write path moved to the shared
    # `apply_environment_probe` service.  It enforces the audit
    # invariant — historical sessions stay immutable, so probes are
    # rejected with 409 once `session.status` is no longer in the
    # allowed set.  Attribution-preservation semantics (don't overwrite
    # a non-null prior value with None) live in the helper too so they
    # can't drift between this endpoint and the recon equivalent.
    apply_environment_probe(
        session=session,
        body=body,
        request=request,
        agent=agent,
        active_statuses={
            ExecutionSessionStatus.ACTIVE.value,
            ExecutionSessionStatus.PAUSED.value,
        },
        session_kind="execution",
    )
    db.commit()
    db.refresh(session)

    return EnvironmentProbeResponse(
        session_id=session.id,
        session_type="execution",
        probed_at=session.environment_probed_at,
        probed_by_user_id=session.environment_probed_by_user_id,
        probed_from_ip=session.environment_probed_from_ip,
        environment=EnvironmentSummary(**session.environment),
    )


# --- Execution context ---

@router.get(
    "/test-plans/{plan_id}/execution-context",
    response_model=ExecutionContextResponse,
    summary="Get execution context for running approved tests",
)
def get_execution_context(
    plan_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Return the plan's entries with per-test detail and known services,
    ready for the agent to work through host-by-host.

    Commands in `proposed_tests` have the `{ip}` placeholder resolved
    to the actual host IP so the agent can present concrete commands
    to the user for approval.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not your test plan")
    if plan.status not in ("approved", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail=f"Plan is in {plan.status} status — must be approved or in_progress to execute.",
        )

    # Find the active execution session for this plan.
    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.test_plan_id == plan.id,
            ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        )
        .first()
    )
    if not session:
        raise HTTPException(
            status_code=400,
            detail="No active execution session for this plan. Start one via the UI first.",
        )

    entries = (
        db.query(TestPlanEntry)
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .order_by(
            case(
                (TestPlanEntry.priority == "critical", 0),
                (TestPlanEntry.priority == "high", 1),
                (TestPlanEntry.priority == "medium", 2),
                (TestPlanEntry.priority == "low", 3),
                (TestPlanEntry.priority == "info", 4),
                else_=5,
            ),
        )
        .all()
    )

    # Load hosts + known services in batch.
    host_ids = [e.host_id for e in entries]
    hosts_map: Dict[int, models.Host] = {}
    if host_ids:
        hosts = (
            db.query(models.Host)
            .filter(models.Host.id.in_(host_ids))
            .all()
        )
        hosts_map = {h.id: h for h in hosts}

    # Open ports per host for known_services.
    svc_map: Dict[int, list] = {}
    if host_ids:
        ports = (
            db.query(models.Port)
            .filter(
                models.Port.host_id.in_(host_ids),
                models.Port.state == "open",
            )
            .order_by(models.Port.host_id, models.Port.port_number)
            .all()
        )
        for p in ports:
            svc_map.setdefault(p.host_id, []).append({
                "port": p.port_number,
                "protocol": p.protocol or "tcp",
                "service": p.service_name,
                "product": p.service_product,
                "version": p.service_version,
            })

    # Existing sanity checks for this session.  An entry can carry
    # multiple checks (different methods — banner_grab + reverse_dns +
    # network_context), enabled by the (session, entry, method) unique
    # constraint in v2.21.0.  v2.25.0 — collapse with an explicit
    # "any passed" rule: a host is considered verified once any
    # method confirms reachability.  The previous dict-comprehension
    # was nondeterministic (last row wins; row order undefined) and
    # could flip the sanity_check_passed signal between requests.
    existing_checks: Dict[int, bool] = {}
    for sc in db.query(HostSanityCheck).filter(
        HostSanityCheck.execution_session_id == session.id,
    ).all():
        existing_checks[sc.entry_id] = existing_checks.get(sc.entry_id, False) or bool(sc.passed)

    # Existing test results for this session.
    existing_results: Dict[int, Dict[int, str]] = {}
    for r in db.query(TestExecutionResult).filter(
        TestExecutionResult.execution_session_id == session.id,
    ).all():
        existing_results.setdefault(r.entry_id, {})[r.test_index] = r.status

    result_hosts = []
    for entry in entries:
        host = hosts_map.get(entry.host_id)
        ip = host.ip_address if host else "unknown"
        # Resolve {ip} placeholders in commands.  v2.25.0 — also handle
        # bare-string entries that ``ProposedTestItem = Union[str,
        # ProposedTest]`` still allows: historical plans (and any agent
        # that wrote a free-form test string) silently disappeared from
        # the executable list before because we only emitted dicts.
        # Coerce strings to a minimal structured shape so they appear
        # in the agent's execution context with their test_index intact.
        tests = []
        for idx, t in enumerate(entry.proposed_tests or []):
            if isinstance(t, dict):
                resolved = dict(t)
            elif isinstance(t, str):
                resolved = {"tool": "unknown", "description": t, "command": t}
            else:
                # Genuinely unknown shape — surface a placeholder so
                # the index numbering stays aligned with the plan's
                # proposed_tests array and the agent sees that
                # something is here.
                resolved = {"tool": "unknown", "description": "(unstructured test entry)"}
            if resolved.get("command"):
                resolved["command"] = resolved["command"].replace("{ip}", ip)
            resolved["test_index"] = idx
            resolved["result_status"] = existing_results.get(entry.id, {}).get(idx)
            tests.append(resolved)

        result_hosts.append(ExecutionHostContext(
            entry_id=entry.id,
            host_id=entry.host_id,
            ip_address=ip,
            hostname=host.hostname if host else None,
            os_name=host.os_name if host else None,
            priority=entry.priority,
            test_phase=entry.test_phase,
            entry_status=entry.status,
            sanity_check_passed=existing_checks.get(entry.id),
            tests=tests,
            known_services=svc_map.get(entry.host_id, []),
        ))

    plan_dict = {
        "id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "entry_count": len(entries),
    }

    return ExecutionContextResponse(
        plan=plan_dict,
        session_id=session.id,
        agent_name=agent.name,
        prompt_version=PROMPT_VERSION,
        hosts=result_hosts,
        # v2.23.0 — echo the per-session environment probe.  None until
        # the agent posts /execution-sessions/{id}/environment.
        environment=(
            EnvironmentSummary(**session.environment)
            if session.environment else None
        ),
    )


# --- Sanity check ---

@router.post(
    "/test-plans/{plan_id}/entries/{entry_id}/sanity-check",
    status_code=201,
    summary="Record per-host target verification before testing",
)
def record_sanity_check(
    body: SanityCheckRequest,
    plan_id: int = Path(..., gt=0),
    entry_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Record a sanity check result for a host before test execution.

    The agent should perform this BEFORE running any tests on a host.
    If `passed` is false, the agent should stop and ask the user for
    guidance.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan or plan.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Plan not found or not yours")

    _require_executable_plan(plan)

    entry = svc.get_entry(entry_id, plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    # B5: bind target_ip to the entry's host so the recorded check can't
    # claim a passing verification for an IP that isn't even this entry's
    # target.  The audit trail's whole point is "we verified THIS host";
    # accepting an arbitrary client-supplied IP defeats it.
    expected_ip = entry.host.ip_address if entry.host else None
    if expected_ip and body.target_ip != expected_ip:
        raise HTTPException(
            status_code=400,
            detail=(
                f"target_ip {body.target_ip!r} does not match the entry's "
                f"host IP {expected_ip!r}. Sanity-check records must be "
                f"bound to the entry's host."
            ),
        )

    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.test_plan_id == plan.id,
            ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=400, detail="No active execution session")

    check = HostSanityCheck(
        execution_session_id=session.id,
        entry_id=entry.id,
        host_id=entry.host_id,
        method=body.method,
        target_ip=body.target_ip,
        port_checked=body.port_checked,
        expected_value=body.expected_value,
        actual_value=body.actual_value,
        source_ip=body.source_ip,
        dns_result=body.dns_result,
        passed=body.passed,
        details=body.details,
    )
    db.add(check)
    try:
        db.commit()
    except IntegrityError:
        # uq_sanity_check_session_entry_method violation — this method was
        # already recorded for this entry.  Re-recording is legitimate (the
        # agent re-ran the check), so update the existing row in place,
        # mirroring record_test_result's upsert fallback.
        db.rollback()
        existing = (
            db.query(HostSanityCheck)
            .filter(
                HostSanityCheck.execution_session_id == session.id,
                HostSanityCheck.entry_id == entry.id,
                HostSanityCheck.method == body.method,
            )
            .first()
        )
        if existing is None:
            raise
        existing.target_ip = body.target_ip
        existing.port_checked = body.port_checked
        existing.expected_value = body.expected_value
        existing.actual_value = body.actual_value
        existing.source_ip = body.source_ip
        existing.dns_result = body.dns_result
        existing.passed = body.passed
        existing.details = body.details
        db.commit()
        return {"id": existing.id, "passed": existing.passed, "updated": True}
    return {"id": check.id, "passed": check.passed, "updated": False}


# --- Test result recording ---

@router.post(
    "/test-plans/{plan_id}/entries/{entry_id}/test-results",
    status_code=201,
    summary="Record one test's execution result",
)
def record_test_result(
    body: TestResultRequest,
    plan_id: int = Path(..., gt=0),
    entry_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Record the result of a single test execution.

    Each test in an entry's `proposed_tests` array is identified by
    `test_index`.  The raw output is truncated to the configured
    `TEST_OUTPUT_MAX_BYTES` (default 100KB, configurable via .env).
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan or plan.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Plan not found or not yours")

    entry = svc.get_entry(entry_id, plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.test_plan_id == plan.id,
            ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=400, detail="No active execution session")

    _require_executable_plan(plan)

    # Validate test_index is within bounds.
    test_count = len(entry.proposed_tests or [])
    if body.test_index < 0 or body.test_index >= test_count:
        raise HTTPException(
            status_code=400,
            detail=f"test_index {body.test_index} out of range (entry has {test_count} tests).",
        )

    # v2.91.0 (code review #2, Option B) — sanity-check gate at
    # result-record time.  Pre-fix the gate only ran at completion
    # (see complete_entry_execution below), which meant raw results
    # could be recorded against an unverified target and the audit
    # trail had no record of WHICH result rows fell into the gap.
    # Option B preserves data (operators frequently have long agentic
    # runs that don't reach completion but produce valuable partials)
    # while requiring a written reason: either there's a passing
    # HostSanityCheck on file, or the agent supplies
    # sanity_override_reason inline and we audit-log the bypass.
    # The reason is persisted on the row so a "show me every result
    # that bypassed sanity" audit query is a one-line WHERE clause.
    sanity_override_reason = (body.sanity_override_reason or "").strip() or None
    # Gate on "does this result assert something about the target" — that
    # is an EXECUTED status OR any finding-bearing result, regardless of
    # status.  Pre-fix the gate only fired for EXECUTED, so an agent could
    # record status="failed"/"skipped" with is_finding=true (and a
    # severity) against an unverified host and the finding would land with
    # sanity_override_reason NULL — invisible to the "show me every
    # bypassed result" audit query.
    requires_sanity = (
        body.status == TestExecutionStatus.EXECUTED.value or bool(body.is_finding)
    )
    if requires_sanity:
        sanity_passed_count = (
            db.query(func.count(HostSanityCheck.id))
            .filter(
                HostSanityCheck.execution_session_id == session.id,
                HostSanityCheck.entry_id == entry.id,
                HostSanityCheck.passed.is_(True),
            )
            .scalar()
            or 0
        )
        if sanity_passed_count == 0 and not sanity_override_reason:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot record an executed result or a finding — no "
                    "passing HostSanityCheck on file for this entry.  Either "
                    "POST a sanity check that returns passed=true first, "
                    "or include `sanity_override_reason` in this request "
                    "explaining why verification wasn't possible (target "
                    "offline, scope change mid-run, etc.).  The override "
                    "is persisted and audit-logged."
                ),
            )
        if sanity_override_reason and sanity_passed_count == 0:
            log_audit_event(
                db,
                user_id=None,  # agent-authenticated, no JWT user
                action="test_result_sanity_override",
                resource_type="test_execution_result",
                resource_id=None,  # row doesn't exist yet
                details={
                    "agent_id": agent.id,
                    "plan_id": plan.id,
                    "entry_id": entry.id,
                    "session_id": session.id,
                    "test_index": body.test_index,
                    "reason": sanity_override_reason[:200],
                },
            )

    # Truncate raw output to configured byte cap.  See _truncate_to_byte_cap
    # at module top for why we slice on bytes, not chars.
    raw_output = _truncate_to_byte_cap(
        body.raw_output or "",
        _settings.TEST_OUTPUT_MAX_BYTES,
    ) or None

    result = TestExecutionResult(
        execution_session_id=session.id,
        entry_id=entry.id,
        test_index=body.test_index,
        status=body.status,
        command_run=body.command_run,
        raw_output=raw_output,
        findings_summary=body.findings_summary,
        severity=body.severity,
        is_finding=body.is_finding,
        sanity_override_reason=sanity_override_reason,
        # v2.43.3 (AUD-O2): executed_at is DateTime(timezone=True); the
        # pre-fix naive `datetime.now()` silently stripped the tz on write
        # and produced naive↔aware comparison bugs downstream (same class
        # as the v2.41.0 auth.py fix).  Use tz-aware now.
        executed_at=(
            datetime.now(timezone.utc)
            if body.status == TestExecutionStatus.EXECUTED.value
            else None
        ),
    )
    db.add(result)
    try:
        db.commit()
    except IntegrityError:
        # uq_exec_result_session_entry_test violation — this test_index was
        # already recorded for this entry.  Re-recording is legitimate, so
        # update the existing row.  Narrowed from a bare ``except Exception``
        # so genuine failures (connection loss, serialization errors) are
        # not silently misread as "row already exists".
        db.rollback()
        existing = (
            db.query(TestExecutionResult)
            .filter(
                TestExecutionResult.execution_session_id == session.id,
                TestExecutionResult.entry_id == entry.id,
                TestExecutionResult.test_index == body.test_index,
            )
            .first()
        )
        if existing:
            existing.status = body.status
            existing.command_run = body.command_run
            existing.raw_output = raw_output
            existing.findings_summary = body.findings_summary
            existing.severity = body.severity
            existing.is_finding = body.is_finding
            # v2.91.0 (#2) — also propagate the override on the
            # re-record path so an operator who re-submits a result
            # AFTER running sanity can clear the bypass marker (by
            # omitting the reason), and one who's re-recording the
            # same bypass keeps it on the row.
            existing.sanity_override_reason = sanity_override_reason
            # v2.43.3 (AUD-O2): also clear executed_at when the row
            # transitions OUT of EXECUTED.  Pre-fix a row that started
            # as 'executed' (got an executed_at timestamp) and was then
            # corrected to 'failed' / 'skipped' / 'pending_approval'
            # kept the stale timestamp, falsely implying the test ran.
            if body.status == TestExecutionStatus.EXECUTED.value:
                existing.executed_at = datetime.now(timezone.utc)
            else:
                existing.executed_at = None
            db.commit()
            return {"id": existing.id, "status": existing.status, "updated": True}
        raise

    return {"id": result.id, "status": result.status, "updated": False}


# --- Complete entry ---

@router.post(
    "/test-plans/{plan_id}/entries/{entry_id}/complete",
    summary="Mark an entry as completed after test execution",
)
def complete_entry_execution(
    body: CompleteEntryRequest,
    plan_id: int = Path(..., gt=0),
    entry_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Mark a test plan entry as completed after the agent finishes
    executing (or skipping) all tests for that host.

    Aggregates per-test results into the entry's `results_data` JSON
    for backward compatibility with the existing entry model.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan or plan.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Plan not found or not yours")

    entry = svc.get_entry(entry_id, plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.test_plan_id == plan.id,
            ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=400, detail="No active execution session")

    _require_executable_plan(plan)

    # Aggregate per-test results into results_data.
    test_results = (
        db.query(TestExecutionResult)
        .filter(
            TestExecutionResult.execution_session_id == session.id,
            TestExecutionResult.entry_id == entry.id,
        )
        .order_by(TestExecutionResult.test_index)
        .all()
    )

    # Sanity-check enforcement (v2.22.0).  Completion requires a passing
    # HostSanityCheck for this entry, OR an explicit ``override_reason``
    # explaining why one wasn't recorded (target down, scope change
    # mid-run, etc.).  Visibility-only mode (which only annotated the
    # gap after the fact) didn't enforce the audit trail's core safety
    # claim that target verification happens before testing is closed.
    sanity_checks = (
        db.query(HostSanityCheck)
        .filter(
            HostSanityCheck.execution_session_id == session.id,
            HostSanityCheck.entry_id == entry.id,
        )
        .all()
    )
    sanity_passed = sum(1 for c in sanity_checks if c.passed)
    override_reason = (body.override_reason or "").strip() or None
    if sanity_passed == 0 and not override_reason:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot complete this entry — no passing HostSanityCheck on file. "
                "Either record a sanity check (POST .../sanity-check) that returns "
                "passed=true, or provide an explicit `override_reason` explaining "
                "why verification wasn't possible (e.g. target offline)."
            ),
        )

    # v2.43.3 (AUD-C2 + AUD-C3): tightened the completion gate.  The
    # v2.25.0 check only rejected the all-or-nothing zero-results case;
    # it let partial-coverage and non-terminal-result completions slide,
    # and the empty-entry case (proposed=0, results=0) bypassed every
    # check.  Every "complete without full evidence" path must now be
    # justified via no_tests_run_reason — symmetric with the sanity-
    # check override_reason invariant above.
    proposed_tests = entry.proposed_tests or []
    proposed_count = len(proposed_tests)
    no_tests_reason = (body.no_tests_run_reason or "").strip() or None

    # Gate 1: zero proposed AND zero results AND no reason → empty-entry
    # black hole the schema comment at agent_schemas.py:453 warned about.
    if proposed_count == 0 and not test_results and not no_tests_reason:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot complete an entry with no proposed tests and no "
                "result rows recorded.  Either add tests to the plan entry "
                "before executing, or pass an explicit `no_tests_run_reason` "
                "to close the entry as deliberately empty (e.g. host out of "
                "scope, covered by another entry, agent declined the host)."
            ),
        )

    # Gate 2: a recorded result is still in a non-terminal state
    # (pending / pending_approval).  Completion freezes results_data into
    # the entry — non-terminal rows would freeze in-flight evidence.
    _TERMINAL_RESULT_STATUSES = {
        TestExecutionStatus.EXECUTED.value,
        TestExecutionStatus.SKIPPED.value,
        TestExecutionStatus.FAILED.value,
        TestExecutionStatus.NOT_APPLICABLE.value,
    }
    non_terminal = [r for r in test_results if r.status not in _TERMINAL_RESULT_STATUSES]
    if non_terminal and not no_tests_reason:
        bad_statuses = sorted({r.status for r in non_terminal})
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot complete this entry — {len(non_terminal)} result row(s) "
                f"are still in a non-terminal state ({', '.join(bad_statuses)}). "
                "Resolve each to executed / skipped / failed / not_applicable "
                "via POST .../test-results, or pass `no_tests_run_reason` to "
                "override (the non-terminal rows will be frozen into results_data "
                "as-is for the audit trail)."
            ),
        )

    # Gate 3: partial coverage — some proposed tests have no result row.
    # Pre-fix a host with 3 proposed tests could be marked completed after
    # a single test result, silently dropping the other two from the
    # evidence record.
    covered_indices = {r.test_index for r in test_results}
    missing_indices = sorted(set(range(proposed_count)) - covered_indices)
    if missing_indices and not no_tests_reason:
        preview = missing_indices[:10]
        suffix = "" if len(missing_indices) <= 10 else f" (+{len(missing_indices) - 10} more)"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot complete this entry — {len(missing_indices)} of "
                f"{proposed_count} proposed test(s) have no result row "
                f"recorded (missing indices: {preview}{suffix}).  Record a "
                "result for each via POST .../test-results, or pass "
                "`no_tests_run_reason` to acknowledge that coverage is "
                "intentionally partial (e.g. target went offline mid-run)."
            ),
        )

    # Legacy gate (v2.25.0) kept for the all-or-nothing case so the error
    # message stays specific.  Covered functionally by Gate 3 but the
    # wording is more actionable when zero results exist.
    if proposed_count > 0 and not test_results and not no_tests_reason:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot complete this entry — {proposed_count} proposed "
                "test(s) but zero TestExecutionResult rows recorded.  Record "
                "results via POST .../test-results before completing, or "
                "provide an explicit `no_tests_run_reason` explaining why "
                "no tests were executed (e.g. target went offline before "
                "any tests began, plan rejected at the host level)."
            ),
        )

    results_data = {
        "session_id": session.id,
        "tests": [
            {
                "test_index": r.test_index,
                "status": r.status,
                "command_run": r.command_run,
                "findings_summary": r.findings_summary,
                "severity": r.severity,
                "is_finding": r.is_finding,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            }
            for r in test_results
        ],
        "total_executed": sum(1 for r in test_results if r.status == TestExecutionStatus.EXECUTED.value),
        "total_skipped": sum(1 for r in test_results if r.status == TestExecutionStatus.SKIPPED.value),
        "total_findings": sum(1 for r in test_results if r.is_finding),
        "sanity_checks_total": len(sanity_checks),
        "sanity_checks_passed": sanity_passed,
        "sanity_check_missing": sanity_passed == 0,
        "override_reason": override_reason,
        "no_tests_run_reason": no_tests_reason,
    }

    # v2.26.0 — route the actual writes through TestPlanService.update_entry
    # so every field change lands in TestPlanHistory and the completed_at
    # timestamp is set by the same lifecycle path every other entry update
    # uses (previously inline here, fragmenting the state machine across
    # the route handler and the service).  body.overall_status is now a
    # validated TestEntryStatus value (the schema uses use_enum_values,
    # so it's already a plain string).
    updates = {
        "status": body.overall_status,
        "results_data": results_data,
    }
    if body.findings_summary:
        updates["findings"] = body.findings_summary
    svc.update_entry(
        entry=entry,
        actor_type="agent",
        actor_id=agent.id,
        updates=updates,
    )

    return {
        "entry_id": entry.id,
        "status": entry.status,
        "tests_executed": results_data["total_executed"],
        "tests_skipped": results_data["total_skipped"],
        "findings_count": results_data["total_findings"],
        "sanity_checks_passed": sanity_passed,
        "sanity_check_missing": sanity_passed == 0,
        "override_reason": override_reason,
        "no_tests_run_reason": no_tests_reason,
    }


# --- Execution progress ---

@router.get(
    "/test-plans/{plan_id}/execution-progress",
    response_model=ExecutionProgressResponse,
    summary="Live execution progress for the current session",
)
def get_execution_progress(
    plan_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Return a summary of execution progress for the active session."""
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan or plan.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Plan not found or not yours")

    session = (
        db.query(ExecutionSession)
        .filter(
            ExecutionSession.test_plan_id == plan.id,
            ExecutionSession.status == ExecutionSessionStatus.ACTIVE.value,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=400, detail="No active execution session")

    # v2.91.1 (code review NEW E) — the pre-fix path did
    # ``db.query(TestPlanEntry).filter(...).all()`` and
    # ``db.query(TestExecutionResult).filter(...).all()`` then counted
    # in Python.  On a 500-entry plan with frequent active-session
    # polling that's full row hydration on every tick — JSON
    # proposed_tests columns + raw_output text for every result —
    # just to derive small integer counts.  Reviewer correctly
    # flagged it as wasteful row materialisation.  Replaced with
    # grouped SQL counts.
    #
    # Entries: one SELECT returning (count_total, count_completed,
    # count_in_progress, sum_proposed_tests).  ``proposed_tests`` is
    # a JSON column; PostgreSQL's ``json_array_length`` gives the
    # cardinality without parsing the array into Python.

    entry_row = (
        db.query(
            func.count(TestPlanEntry.id).label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (TestPlanEntry.status.in_(["completed", "rejected"]), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("completed"),
            func.coalesce(
                func.sum(
                    case(
                        (TestPlanEntry.status == "in_progress", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("in_progress"),
            func.coalesce(
                func.sum(func.json_array_length(TestPlanEntry.proposed_tests)),
                0,
            ).label("total_tests"),
        )
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .one()
    )
    total_entries = entry_row.total or 0
    completed = entry_row.completed or 0
    in_progress = entry_row.in_progress or 0
    total_tests = entry_row.total_tests or 0

    # Results: one GROUP BY status query (small result set — at most
    # ~6 rows even on a 5000-result session) for the per-status
    # buckets, then a separate query for the finding/critical counts
    # (which can't be expressed as part of the status grouping
    # without nesting).  Two cheap queries vs. .all() of every
    # TestExecutionResult row.
    status_rows = (
        db.query(
            TestExecutionResult.status,
            func.count(TestExecutionResult.id).label("n"),
        )
        .filter(TestExecutionResult.execution_session_id == session.id)
        .group_by(TestExecutionResult.status)
        .all()
    )
    status_counts = {row.status: row.n for row in status_rows}
    tests_executed = status_counts.get(TestExecutionStatus.EXECUTED.value, 0)
    tests_skipped = status_counts.get(TestExecutionStatus.SKIPPED.value, 0)
    # v2.25.0 — derive ``tests_pending`` from "proposed minus everything
    # we have a row for" so any new status the enum gains in the future
    # doesn't silently rejoin the pending bucket.
    tests_failed = status_counts.get(TestExecutionStatus.FAILED.value, 0)
    tests_not_applicable = status_counts.get(
        TestExecutionStatus.NOT_APPLICABLE.value, 0,
    )
    tests_pending_approval = status_counts.get(
        TestExecutionStatus.PENDING_APPROVAL.value, 0,
    )
    tests_recorded = sum(status_counts.values())
    tests_pending = max(total_tests - tests_recorded, 0)

    finding_row = (
        db.query(
            func.coalesce(
                func.sum(case((TestExecutionResult.is_finding.is_(True), 1), else_=0)),
                0,
            ).label("findings"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (TestExecutionResult.is_finding.is_(True))
                            & (TestExecutionResult.severity == "critical"),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("critical"),
        )
        .filter(TestExecutionResult.execution_session_id == session.id)
        .one()
    )
    findings_count = finding_row.findings or 0
    critical_findings = finding_row.critical or 0

    return ExecutionProgressResponse(
        plan_id=plan.id,
        session_id=session.id,
        total_entries=total_entries,
        entries_completed=completed,
        entries_in_progress=in_progress,
        entries_remaining=total_entries - completed,
        total_tests=total_tests,
        tests_executed=tests_executed,
        tests_skipped=tests_skipped,
        tests_failed=tests_failed,
        tests_not_applicable=tests_not_applicable,
        tests_pending_approval=tests_pending_approval,
        tests_pending=tests_pending,
        findings_count=findings_count,
        critical_findings=critical_findings,
    )


@router.post(
    "/execution-sessions/{session_id}/complete",
    response_model=ExecutionSessionCompleteResponse,
    summary="Mark this execution session complete",
)
def complete_execution_session(
    body: ExecutionSessionCompleteRequest,
    request: Request,
    session_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Transition the execution session from active/paused to a terminal state (v2.45.2).

    Symmetric with ``/recon/complete``.  Pre-v2.45.2 there was NO
    agent-callable way to mark an execution session done — the session
    model has had ``ExecutionSessionStatus.COMPLETED`` since launch but
    no code path ever wrote it, so sessions stayed ``ACTIVE`` forever
    after the agent submitted the last entry's results.  Operators
    saw "still active" on the runs list with no path to closure
    other than the JWT-side ``/abandon`` endpoint (which semantically
    means "we gave up", not "we finished").

    Closure invariants:

      * Session must currently be ``ACTIVE`` or ``PAUSED`` — calling
        on a session already in a terminal state returns 409 to
        distinguish "first complete" from "duplicate close attempt".
        Aligns with the recon side's idempotency posture.

      * ``overall_status="completed"`` is the happy path: all planned
        entries ran (some may be skipped or failed at the entry level
        — that's normal).  ``"failed"`` is for "the session itself
        broke" — auth lost, scope mismatch, plan invalidated mid-run.
        Agents shouldn't self-abandon; that's a user action via the
        JWT-side endpoint.

      * Notes are appended to whatever's already on the session
        record, capped at 8 KiB total.  Lossy at the cap by design
        — the audit story is in agent_api_calls, the notes field is
        a free-form summary, not a transcript.

      * Final counts are computed and returned in the response so
        the caller has a stable "what landed" view without polling
        ``/progress``.

    The plan's status is NOT automatically transitioned.  Plans live
    longer than sessions (a plan can have multiple execution sessions
    over its lifetime); the user owns the plan-level lifecycle via
    the JWT-side endpoints.
    """
    plan = (
        db.query(TestPlan)
        .filter(
            TestPlan.id.in_(
                db.query(ExecutionSession.test_plan_id)
                .filter(ExecutionSession.id == session_id)
            ),
            TestPlan.project_id == agent.project_id,
        )
        .first()
    )
    session = (
        db.query(ExecutionSession)
        .filter(ExecutionSession.id == session_id)
        .first()
    )
    if not session or not plan or plan.agent_id != agent.id:
        raise HTTPException(
            status_code=404,
            detail="Execution session not found or not bound to your plan",
        )

    # v2.84.1 — the URL is keyed by session_id, not plan_id, so
    # require_plan_scope can't gate this directly (it declares plan_id as
    # a Path() param, which FastAPI then demands in the URL template ->
    # 422 on every request).  Enforce the same audit chain inline, mirroring
    # record_execution_environment above: reject recon/assist keys
    # outright, then verify a plan-scoped key actually targets this
    # session's plan.  Pre-v2.84.1 the route was unreachable due to the
    # path-parameter mismatch.
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    scoped_assist = getattr(request.state, "scoped_assist_session_id", None)
    if scoped_scope is not None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a reconnaissance run and cannot "
                "complete execution sessions. Use the plan-scoped key minted "
                "by /execute, or the unscoped agent key."
            ),
        )
    if scoped_assist is not None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is scoped to a read-only assist session and "
                "cannot complete execution sessions."
            ),
        )
    scoped_plan = getattr(request.state, "scoped_plan_id", None)
    if scoped_plan is not None and scoped_plan != plan.id:
        raise HTTPException(
            status_code=403,
            detail="This API key is scoped to a different test plan.",
        )

    terminal_states = {
        ExecutionSessionStatus.COMPLETED.value,
        ExecutionSessionStatus.ABANDONED.value,
        ExecutionSessionStatus.FAILED.value,
    }
    if session.status in terminal_states:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Execution session #{session_id} is already in terminal "
                f"state '{session.status}'; cannot complete twice. "
                f"If you need a fresh session, start one via the UI."
            ),
        )

    now = datetime.now(timezone.utc)

    if body.notes:
        addition = body.notes.strip()
        if addition:
            session.notes = (
                (session.notes + "\n\n" + addition) if session.notes else addition
            )[:8192]

    # Pick the terminal state.  body.overall_status is constrained by
    # the schema's Literal to "completed" | "failed".
    if body.overall_status == "failed":
        session.status = ExecutionSessionStatus.FAILED.value
    else:
        session.status = ExecutionSessionStatus.COMPLETED.value
    session.completed_at = now

    # Final counters in one read so the response is stable even when
    # subsequent /progress calls 400 (the session is no longer ACTIVE,
    # so the ACTIVE-only loader rejects them).  Grouped SQL counts —
    # NOT ``.all()`` + Python counting — so we don't hydrate every
    # entry's JSON proposed_tests column just to derive two integers
    # (same fix as get_execution_progress, v2.91.1).
    entry_counts = (
        db.query(
            func.count(TestPlanEntry.id).label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (TestPlanEntry.status.in_(["completed", "rejected"]), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("completed"),
        )
        .filter(TestPlanEntry.test_plan_id == plan.id)
        .one()
    )
    entries_total = int(entry_counts.total or 0)
    entries_completed = int(entry_counts.completed or 0)
    entries_remaining = entries_total - entries_completed

    tests_recorded = (
        db.query(func.count(TestExecutionResult.id))
        .filter(TestExecutionResult.execution_session_id == session.id)
        .scalar()
    ) or 0
    findings_count = (
        db.query(func.count(TestExecutionResult.id))
        .filter(
            TestExecutionResult.execution_session_id == session.id,
            TestExecutionResult.is_finding.is_(True),
        )
        .scalar()
    ) or 0

    db.commit()
    db.refresh(session)

    # Nudge the plan stewards when the agent has worked through every entry —
    # the session-complete path knows entries_remaining == 0 but (by design)
    # won't auto-close the plan, so signal that it's ready. Best-effort; only
    # while the plan is still open, so re-running a finished plan doesn't spam.
    if (
        session.status == ExecutionSessionStatus.COMPLETED.value
        and entries_remaining == 0
        and entries_total > 0
        and plan.status in ("approved", "in_progress")
    ):
        try:
            NotificationService(db).notify_plan_ready_to_close(plan, agent.project_id)
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("Failed to notify plan-ready-to-close for plan %s", plan.id, exc_info=True)

    return ExecutionSessionCompleteResponse(
        session_id=session.id,
        test_plan_id=plan.id,
        status=session.status,
        completed_at=session.completed_at,
        entries_total=entries_total,
        entries_completed=entries_completed,
        entries_remaining=entries_remaining,
        tests_recorded=tests_recorded,
        findings_count=findings_count,
    )
