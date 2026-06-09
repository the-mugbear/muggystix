"""
Agent API — test plan endpoints (agent-facing).

Create / list / read / update test plans and their entries, plus the
planning context and pre-submit validation.  Split out of agent_api.py.
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models_agent import Agent, TestPlan, TestPlanEntry
from app.api.deps import check_agent_rate_limit, deny_scoped_keys, require_plan_scope
from app.services.test_plan_service import TestPlanService

from app.api.v1.endpoints.agent_schemas import (
    VulnCounts, VulnBrief, PortTuple, CandidateHost, PlanningContext,
    PlanCreate, PlanUpdate, EntryBatch, EntryCreate, AgentEntryUpdate,
    PlanResponse, EntryResponse, PlanDetailResponse,
    EntryBatchResponse, CoverageInfo, PreSubmitReport,
)
from app.api.v1.endpoints.agent_common import (
    _apply_agent_host_filters, _batch_host_enrichment, _plan_response,
)

router = APIRouter()


# High-value ports that qualify medium-vuln hosts for inclusion in
# selection policy, and that should float to the top of a host's
# inferred-service hint list in /context.  Used by /context (selection
# policy evaluation) and /validate (coverage split).  Keep in sync with
# the AGENTS.md selection-policy description.
_HIGH_VALUE_PORTS = {445, 139, 3389, 3306, 1433, 5432, 1521, 27017, 6379, 5900}

# Fallback service names for high-value ports that nmap's service
# detection sometimes misses.  Used by /context's
# ``inferred_service_hints`` field so an agent can rationalize policy
# decisions even when ``ports[].service`` is null.  Only covers the
# ports BlueStick actually treats as high-value — this is NOT a
# general /etc/services replacement.
_HIGH_VALUE_PORT_HINTS: Dict[int, str] = {
    139: "netbios-ssn",
    445: "smb",
    3389: "rdp",
    3306: "mysql",
    1433: "mssql",
    5432: "postgresql",
    1521: "oracle",
    27017: "mongodb",
    6379: "redis",
    5900: "vnc",
}


def _evaluate_host_policy(
    vuln_counts: Dict[str, int],
    service_names: List[str],
    open_port_nums: set,
) -> bool:
    """Return True if a host meets the selection policy.

    Policy (documented in AGENTS.md):
      - any critical or high vuln → include
      - medium vuln qualifies only if the host exposes multiple
        services OR a high-value port (SMB, RDP, databases, etc.)
      - anything else → exclude

    Extracted so /context and /validate apply the *exact same* check.
    Pass counts from _batch_host_enrichment.
    """
    crit_or_high = vuln_counts.get("critical", 0) + vuln_counts.get("high", 0) > 0
    if crit_or_high:
        return True
    medium_qualified = (
        vuln_counts.get("medium", 0) > 0
        and (len(service_names) >= 2 or bool(open_port_nums & _HIGH_VALUE_PORTS))
    )
    return medium_qualified


# ---------------------------------------------------------------------------
# Test plan endpoints (agent-facing)
# ---------------------------------------------------------------------------

def _entry_response(entry: TestPlanEntry) -> EntryResponse:
    host = entry.host
    return EntryResponse(
        id=entry.id,
        host_id=entry.host_id,
        host_ip=host.ip_address if host else None,
        priority=entry.priority,
        test_phase=entry.test_phase,
        proposed_tests=entry.proposed_tests or [],
        rationale=entry.rationale,
        status=entry.status,
        findings=entry.findings,
        results_data=entry.results_data,
        notes=entry.notes,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.post("/test-plans", response_model=PlanResponse, status_code=201, summary="Create a test plan")
def create_test_plan(
    body: PlanCreate,
    agent: Agent = Depends(deny_scoped_keys),
    db: Session = Depends(get_db),
):
    """Create a test plan (unscoped keys only).

    Per-plan-scoped keys (minted by ``/test-plans/generate`` or
    ``/test-plans/{id}/execute``) are rejected here: a key that belongs
    to an existing plan has no business spawning a new one.  Use a
    JWT-authenticated ``POST /projects/{id}/test-plans/generate`` or an
    unscoped agent key instead.
    """
    svc = TestPlanService(db)
    plan = svc.create_plan(
        project_id=agent.project_id,
        agent_id=agent.id,
        title=body.title,
        description=body.description,
        actor_type="agent",
        actor_id=agent.id,
    )
    return _plan_response(plan, db)


@router.get("/test-plans", response_model=List[PlanResponse], summary="List own test plans")
def list_test_plans(
    request: Request,
    status: Optional[str] = Query(None),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    # Reject recon-/assist-scoped keys explicitly.  Every other
    # /test-plans/* route runs through require_plan_scope, which denies
    # these; this list route only depends on check_agent_rate_limit, so
    # without this guard a recon-scoped key (scoped_plan_id is None) would
    # fall through the per-plan filter below and receive the agent's full
    # plan list — breaking the plan/recon workflow isolation contract.
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    scoped_assist = getattr(request.state, "scoped_assist_session_id", None)
    if scoped_scope is not None or scoped_assist is not None:
        raise HTTPException(
            status_code=403,
            detail="This API key is scoped to a recon/assist session, not plan generation.",
        )

    svc = TestPlanService(db)
    plans = svc.list_plans(agent.project_id, status_filter=status, agent_id=agent.id)
    # Per-plan-scoped keys only see their own plan in the listing.
    scoped = getattr(request.state, "scoped_plan_id", None)
    if scoped is not None:
        plans = [p for p in plans if p.id == scoped]
    return [_plan_response(p, db) for p in plans]


@router.get("/test-plans/{plan_id}", response_model=PlanDetailResponse, summary="Get test plan")
def get_test_plan(
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
    return PlanDetailResponse(
        id=plan.id,
        version=plan.version,
        title=plan.title,
        description=plan.description,
        status=plan.status,
        entry_count=len(plan.entries),
        completion_pct=svc.get_progress(plan.id)["completion_pct"],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        entries=[_entry_response(e) for e in plan.entries],
    )


@router.get(
    "/test-plans/{plan_id}/context",
    response_model=PlanningContext,
    summary="Get planning context for a test plan",
)
def get_planning_context(
    plan_id: int = Path(..., gt=0),
    limit: int = Query(500, ge=1, le=2000),
    after_host_id: Optional[int] = Query(
        None, description="Cursor for pagination — return only hosts with id > this value. "
        "Use the last host id from the previous page to fetch the next batch."),
    include_zero_port: bool = Query(
        False, description="Include hosts with no open ports (excluded by default)"),
    # v2.90.4 (code review #5) — Literal-typed so a typo
    # (detail_level=summary) is rejected at the boundary with 422
    # instead of silently taking the expensive ``full`` path.
    detail_level: Literal["brief", "full"] = Query(
        "full", description="'brief' returns summary fields only (no ports array); "
        "'full' (default) includes full port details per host. Use brief for "
        "candidate selection, full for the hosts you'll create entries for."),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Return plan metadata + candidate hosts for entry creation.

    Candidate hosts are those in the agent's project that match the plan's
    stored filter criteria (if any) and are NOT already in the plan.
    Each host includes open port count, vulnerability severity counts,
    top critical/high vulnerabilities with titles and CVEs, and a
    deduplicated list of open service names.

    By default, hosts with zero open ports are excluded since they offer
    no actionable attack surface.  Pass ``include_zero_port=true`` to
    include them.

    This replaces the multi-call discovery pattern (dashboard → hosts → plan
    entries cross-reference) with a single request.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not your test plan")

    filters = plan.filter_criteria or {}

    # Build filtered host query
    q = db.query(models.Host).filter(models.Host.project_id == agent.project_id)
    q = _apply_agent_host_filters(
        q, db,
        state=filters.get("state"),
        ports=filters.get("ports"),
        services=filters.get("services"),
        subnets=filters.get("subnets"),
        has_critical_vulns=filters.get("has_critical_vulns"),
        has_high_vulns=filters.get("has_high_vulns"),
        search=filters.get("search"),
        not_in_plan_id=plan_id,
    )

    # Exclude zero-port hosts by default — they have no actionable surface
    if not include_zero_port:
        q = q.filter(
            models.Host.id.in_(
                db.query(models.Port.host_id)
                .filter(models.Port.state == "open")
                .distinct()
            )
        )

    total_project_hosts = (
        db.query(func.count(models.Host.id))
        .filter(models.Host.project_id == agent.project_id)
        .scalar()
    )
    already_in_plan = (
        db.query(func.count(TestPlanEntry.id))
        .filter(TestPlanEntry.test_plan_id == plan_id)
        .scalar()
    )

    # Count matching before applying cursor + limit.
    total_matching = q.count()

    # Cursor-based pagination: if after_host_id is set, skip all hosts
    # with id <= that value.  This lets agents page through large
    # candidate sets in batches of `limit` without re-scanning from
    # the beginning each time.  Combined with the existing
    # `not_in_plan_id` filter, this also supports resumption — a
    # second agent session can pick up where the first left off.
    if after_host_id is not None:
        q = q.filter(models.Host.id > after_host_id)

    hosts = q.order_by(models.Host.id).limit(limit).all()

    # Batch-enrich — in "brief" mode skip the expensive per-host port
    # details and top-vuln lookups since the caller only needs summary
    # fields for candidate selection (id, ip, vuln counts, meets_policy).
    is_brief = detail_level == "brief"
    host_ids = [h.id for h in hosts]
    port_counts, vuln_map, svc_map, port_details, top_vulns = _batch_host_enrichment(
        db, host_ids, include_ports=not is_brief,
    )

    # Even in brief mode, we still need open-port numbers to evaluate the
    # selection policy honestly — a medium-vuln host that qualifies only
    # because it exposes a high-value port (SMB/RDP/etc.) was previously
    # marked meets_policy=false in brief mode, disagreeing with full mode
    # and with /validate.  A single name+number column read is cheap.
    port_nums_by_host: Dict[int, set] = {}
    if is_brief and host_ids:
        for hid, pn in (
            db.query(models.Port.host_id, models.Port.port_number)
            .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
            .all()
        ):
            port_nums_by_host.setdefault(hid, set()).add(pn)

    candidates = []
    policy_match_count = 0
    for h in hosts:
        vc = vuln_map.get(h.id, {})
        # In brief mode, skip the expensive per-host port list and
        # top-vuln list — the agent only needs them when it's actually
        # building entries for selected hosts, not when it's scanning
        # the candidate list.
        if is_brief:
            host_ports = []
            host_top_vulns = []
        else:
            host_ports = [
                PortTuple(
                    port=p.port_number,
                    protocol=p.protocol or "tcp",
                    state=p.state or "unknown",
                    service=p.service_name,
                    product=p.service_product,
                    version=p.service_version,
                )
                for p in port_details.get(h.id, [])
            ]
            host_top_vulns = [
                VulnBrief(
                    title=v.title,
                    severity=v.severity.value if hasattr(v.severity, "value") else v.severity,
                    cve_id=v.cve_id,
                )
                for v in top_vulns.get(h.id, [])
            ]
        # Evaluate selection policy with the same inputs in both modes —
        # brief mode pulls just the port-number column above so the
        # high-value-port check matches what /validate and full mode see.
        if is_brief:
            host_port_nums: set = port_nums_by_host.get(h.id, set())
        else:
            host_port_nums = {p.port for p in host_ports}
        host_svcs = svc_map.get(h.id, [])
        host_meets_policy = _evaluate_host_policy(vc, host_svcs, host_port_nums)
        if host_meets_policy:
            policy_match_count += 1

        # Build the inferred-service hint list.  Agent feedback: nmap
        # sometimes returns null service names on high-value ports, so
        # the agent has to guess port→service.  We compute the hints
        # server-side: any open port in _HIGH_VALUE_PORT_HINTS where
        # the actual service name is missing gets the canonical name.
        # Ports with a real service detection pass through unchanged.
        # Only populated in detailed mode — brief mode doesn't have
        # per-port data anyway.
        hint_entries: List[Dict[str, Any]] = []
        if not is_brief:
            for p in host_ports:
                hint = _HIGH_VALUE_PORT_HINTS.get(p.port)
                if hint is None:
                    continue
                # Only surface the hint if the real service is absent
                # or clearly wrong (empty string, "unknown").
                actual = (p.service or "").strip().lower()
                if actual and actual not in ("unknown", "tcpwrapped"):
                    continue
                hint_entries.append({
                    "port": p.port,
                    "protocol": p.protocol,
                    "inferred_service": hint,
                    "source": "port_number_heuristic",
                })

        candidates.append(CandidateHost(
            id=h.id,
            ip_address=h.ip_address,
            hostname=h.hostname,
            os_name=h.os_name,
            open_port_count=port_counts.get(h.id, 0),
            vuln_summary=VulnCounts(
                critical=vc.get("critical", 0),
                high=vc.get("high", 0),
                medium=vc.get("medium", 0),
                low=vc.get("low", 0),
            ),
            top_vulnerabilities=host_top_vulns,
            services=sorted(host_svcs),
            ports=host_ports,
            meets_policy=host_meets_policy,
            inferred_service_hints=hint_entries,
        ))

    # Pagination metadata — the agent uses `has_more` to decide
    # whether to make another /context call with after_host_id.
    last_host_id = hosts[-1].id if hosts else None
    has_more = len(hosts) == limit  # if we got a full page, there may be more

    progress = svc.get_progress(plan_id)
    plan_resp = PlanResponse(
        id=plan.id,
        version=plan.version,
        title=plan.title,
        description=plan.description,
        status=plan.status,
        entry_count=progress["total_entries"],
        completion_pct=progress["completion_pct"],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )
    # Machine-readable entry-creation contract.  Previously the agent had
    # to infer the POST /entries body shape from AGENTS.md examples
    # alone; returning a concrete template + JSON schema in the same
    # response removes the guesswork.  Picking a real host id from the
    # current candidate list so the example is "press send" valid.
    sample_host_id = candidates[0].id if candidates else 0
    entry_template = {
        "host_id": sample_host_id,
        "priority": "high",
        "test_phase": "enumeration",
        "proposed_tests": [
            {
                "tool": "netexec",
                # Targeted VALIDATION against the already-known open 445 —
                # not a re-scan. Recon already recorded the port/service;
                # this exercises it (see the "build on recon, don't
                # re-discover" guidance in the plan-gen prompt).
                "description": "Validate anonymous/null-session SMB access on the already-open 445",
                "command": "netexec smb {ip} -u '' -p '' --shares",
                "expected_result": "Share list with READ/WRITE markers, or an explicit access-denied",
                "references": [
                    "https://www.netexec.wiki/smb-protocol/enumerating-shares",
                ],
            },
        ],
        "rationale": (
            "Why this host needs these tests — quote the relevant clause "
            "from selection_policy and cite observed services / vulns from "
            "candidate_hosts[].ports / .top_vulnerabilities. Target the "
            "already-known open ports; do not propose discovery/version "
            "re-scans (recon already ran them)."
        ),
        "notes": "Optional free-form context — omit the field if none.",
    }
    entry_batch_example = {"entries": [entry_template]}
    entry_schema = EntryCreate.model_json_schema()

    return PlanningContext(
        plan=plan_resp.model_dump(mode="json"),
        filter_criteria=plan.filter_criteria,
        agent_name=agent.name,
        selection_policy=(
            "Create entries for all hosts with critical or high vulnerabilities. "
            "Include hosts with medium vulnerabilities if they expose multiple "
            "services or high-value ports (SMB, RDP, databases). "
            "Skip hosts with zero open ports unless explicitly included via "
            "include_zero_port=true. "
            "For each entry, provide tool-specific commands with {ip} placeholders "
            "and explain what constitutes a finding versus a pass."
        ),
        summary={
            "total_hosts": total_project_hosts,
            "matching_filter": total_matching,
            "already_in_plan": already_in_plan,
            "candidates_reviewed": len(candidates),
            "policy_match_count": policy_match_count,
            "zero_port_excluded": not include_zero_port,
            "detail_level": detail_level,
            "has_more": has_more,
            "next_cursor": last_host_id if has_more else None,
        },
        candidate_hosts=candidates,
        entry_template=entry_template,
        entry_batch_example=entry_batch_example,
        entry_schema=entry_schema,
    )


@router.patch("/test-plans/{plan_id}", response_model=PlanResponse, summary="Update test plan metadata")
def update_test_plan(
    body: PlanUpdate,
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

    plan = svc.update_plan(
        plan, "agent", agent.id,
        title=body.title,
        description=body.description,
        generated_by_model=body.generated_by_model,
        generated_by_tool=body.generated_by_tool,
        prompt_version=body.prompt_version,
    )
    return _plan_response(plan, db)


@router.post(
    "/test-plans/{plan_id}/entries",
    response_model=EntryBatchResponse,
    status_code=201,
    summary="Batch-add entries to a test plan",
)
def add_entries(
    body: EntryBatch,
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

    entries_data = [e.model_dump() for e in body.entries]
    try:
        created = svc.add_entries(plan, entries_data, "agent", agent.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return EntryBatchResponse(entries=[_entry_response(e) for e in created])


@router.patch(
    "/test-plans/{plan_id}/entries/{entry_id}",
    response_model=EntryResponse,
    summary="Update a test plan entry",
)
def update_entry(
    body: AgentEntryUpdate,
    plan_id: int = Path(..., gt=0),
    entry_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not your test plan")

    entry = svc.get_entry(entry_id, plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    # Agents cannot approve entries — only set proposed/in_progress/completed/rejected
    if body.status and body.status == "approved":
        raise HTTPException(status_code=403, detail="Agents cannot approve entries")

    updates = body.model_dump(exclude_none=True, exclude={"expected_updated_at"})
    if not updates:
        return _entry_response(entry)

    try:
        entry = svc.update_entry(
            entry, "agent", agent.id, updates,
            expected_updated_at=body.expected_updated_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return _entry_response(entry)


@router.get(
    "/test-plans/{plan_id}/validate",
    response_model=PreSubmitReport,
    summary="Dry-run validation before submit",
)
def validate_test_plan(
    plan_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_plan_scope),
    db: Session = Depends(get_db),
):
    """Return summary stats and warnings without changing state.

    Call this before ``/submit`` to catch common issues: empty plans,
    missing description, low-quality entries, etc.
    """
    svc = TestPlanService(db)
    plan = svc.get_plan(plan_id, agent.project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Test plan not found")
    if plan.agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not your test plan")

    progress = svc.get_progress(plan.id)
    warnings: List[str] = []

    if progress["total_entries"] == 0:
        warnings.append("Plan has no entries — add entries before submitting.")
    if not plan.description:
        warnings.append(
            "Plan has no description. PATCH the plan with a description "
            "summarizing scope and prioritization before submitting."
        )
    # Check for entries with very short rationale
    short_rationales = (
        db.query(func.count(TestPlanEntry.id))
        .filter(
            TestPlanEntry.test_plan_id == plan_id,
            func.length(TestPlanEntry.rationale) < 30,
        )
        .scalar()
    )
    if short_rationales:
        warnings.append(
            f"{short_rationales} entries have rationale under 30 characters — "
            "consider adding more detail."
        )

    # Coverage split (v2.10.0).  The agent-feedback review flagged
    # the old single "eligible_hosts_remaining" number as confusing
    # because it lumped "hosts you missed" together with "hosts you
    # correctly skipped per policy".  Now we compute both buckets
    # against the same policy function /context uses.
    #
    # Step 1: collect every host in the project with at least one
    # open port, excluding hosts already in the plan.
    remaining_host_rows = (
        db.query(models.Host)
        .filter(
            models.Host.project_id == agent.project_id,
            models.Host.id.in_(
                db.query(models.Port.host_id)
                .filter(models.Port.state == "open")
                .distinct()
                .scalar_subquery()
            ),
            ~models.Host.id.in_(
                db.query(TestPlanEntry.host_id)
                .filter(TestPlanEntry.test_plan_id == plan_id)
                .scalar_subquery()
            ),
        )
        .all()
    )

    policy_matching_remaining = 0
    non_policy_with_open_ports = 0
    if remaining_host_rows:
        remaining_ids = [h.id for h in remaining_host_rows]
        # We need port numbers for the high-value-port check, so pass
        # include_ports=True.  This is the pre-submit path, not a
        # hot loop — the extra query is acceptable.
        r_port_counts, r_vuln_map, r_svc_map, r_port_details, _ = _batch_host_enrichment(
            db, remaining_ids, include_ports=True,
        )
        for h in remaining_host_rows:
            vc = r_vuln_map.get(h.id, {})
            port_nums = {p.port_number for p in r_port_details.get(h.id, [])}
            svcs = r_svc_map.get(h.id, [])
            if _evaluate_host_policy(vc, svcs, port_nums):
                policy_matching_remaining += 1
            else:
                non_policy_with_open_ports += 1

    remaining_eligible = policy_matching_remaining + non_policy_with_open_ports
    total_eligible = progress["total_entries"] + remaining_eligible
    coverage_pct = round(
        (progress["total_entries"] / total_eligible) * 100, 1
    ) if total_eligible else 100.0

    coverage_note = None
    if policy_matching_remaining > 0:
        # This is the actionable case — real missed scope.
        coverage_note = (
            f"Plan covers {progress['total_entries']} hosts. "
            f"{policy_matching_remaining} additional host(s) match the "
            f"selection policy (critical/high vulns, or medium with a "
            f"high-value port) and are NOT in the plan. These look like "
            f"missed coverage — page through /context with after_host_id "
            f"to pick them up, or if the exclusion is intentional, add "
            f"a description note explaining why. "
            f"(Separately, {non_policy_with_open_ports} host(s) have "
            f"open ports but don't meet the policy — those are correctly "
            f"skipped and do not need entries.)"
        )
    elif non_policy_with_open_ports > 0:
        # Informational only — the agent did the right thing.
        coverage_note = (
            f"Plan covers all {progress['total_entries']} policy-matching "
            f"host(s). {non_policy_with_open_ports} additional host(s) "
            f"have open ports but don't meet the selection policy (no "
            f"crit/high vulns, no qualifying medium) — correctly skipped."
        )

    return PreSubmitReport(
        plan_id=plan.id,
        # Coverage is informational — it doesn't block readiness.
        # Only real warnings (empty plan, missing description, short
        # rationale) block.
        ready=len(warnings) == 0,
        total_entries=progress["total_entries"],
        by_priority=progress["by_priority"],
        by_phase=progress["by_phase"],
        warnings=warnings,
        coverage=CoverageInfo(
            entries_in_plan=progress["total_entries"],
            eligible_hosts_remaining=remaining_eligible,
            policy_matching_remaining=policy_matching_remaining,
            non_policy_with_open_ports=non_policy_with_open_ports,
            coverage_pct=coverage_pct,
            note=coverage_note,
        ),
    )
