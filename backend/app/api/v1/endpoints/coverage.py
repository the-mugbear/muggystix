"""
Project coverage endpoint (v3 alpha.3).

Answers the question the v3 Operations page leads with: across this
project, *how much* of the host universe has been touched by each
stage of the pipeline?

Three coverage dimensions:

    plan_covered       — host has at least one TestPlanEntry
    execution_covered  — host has at least one TestExecutionResult
                          (joined through its TestPlanEntry)
    scope_coverage     — for each scope, IPs in the CIDR ranges vs
                          discovered hosts in those ranges

Recon coverage is intentionally *not* expressed as "host has a
HostScanHistory row" — every host in the project, by definition, was
discovered by some scan, which would make recon-covered == total and
add no signal.  The recon dimension is captured by ``scope_coverage``
(how much of the declared scope has been swept) plus
``hosts_outside_scope`` (hosts found but outside any declared scope).

The endpoint is read-only, project-scoped, and intentionally cheap
(four aggregate queries) so the Operations page can poll it every few
seconds while runs are in flight.

This is consumed by:
- v3 Operations "Host coverage" section
- v3 Hosts list "Show only ..." filter chips (no-plan / no-execution)
- alpha.6 Recon Run Detail (scope_coverage subset)
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.api.deps import get_current_project
from app.api.v1.endpoints.auth import get_current_user
from app.db.models import (
    Host,
    HostSubnetMapping,
    Scope,
    Subnet,
)
from app.db.models_agent import (
    TestExecutionResult,
    TestPlanEntry,
)
from app.db.models_auth import User
from app.db.models_project import Project
from app.db.session import get_db


router = APIRouter()


class ScopeCoverage(BaseModel):
    """Per-scope coverage row.

    ``total_scoped_ips`` is the sum of /32 hosts across this scope's
    CIDR ranges — naive count, no exclusion for network/broadcast
    addresses.  ``discovered_in_scope`` is distinct hosts mapped to
    any of the scope's subnets (via HostSubnetMapping).
    """
    scope_id: int
    scope_name: str
    subnet_count: int = 0
    total_scoped_ips: int = 0
    discovered_in_scope: int = 0
    # 0–100, derived.  ``None`` when total_scoped_ips is 0 (an empty
    # scope) so the UI can render "—" instead of a meaningless 0%.
    coverage_percent: Optional[float] = None


class ProjectCoverageResponse(BaseModel):
    """Project-wide coverage summary (v3 alpha.3).

    Hosts-of-record (``total_hosts``) include every host the project
    has ever seen — recon, manual upload, anything.  The two pipeline
    stage counts (``hosts_with_plan_entry``,
    ``hosts_with_execution_result``) are derived from agent-facing
    tables and so are zero for a project that's only been through
    bare ingestion.

    ``hosts_no_plan`` and ``hosts_no_execution`` are the *gap*
    counts — they exist as their own keys so the UI doesn't have to
    re-derive ``total_hosts - covered`` and risk drift if the
    derivation changes.
    """
    project_id: int

    total_hosts: int = 0
    hosts_with_plan_entry: int = 0
    hosts_with_execution_result: int = 0
    hosts_no_plan: int = 0
    hosts_no_execution: int = 0

    # Scope-level coverage (what fraction of declared scope has been
    # discovered).  Empty list when the project has no scopes declared.
    total_scopes: int = 0
    scopes: List[ScopeCoverage] = Field(default_factory=list)
    # Hosts found in the project but not mapped to any scope's subnet.
    # Useful for "things we found that we shouldn't have" or
    # "out-of-scope drift".
    hosts_outside_scope: int = 0


def _cidr_size(cidr: str) -> int:
    """Return /N → 2^(32-N) for IPv4, 2^(128-N) for IPv6.

    Hand-parsed instead of using ipaddress.ip_network because we only
    need the size and the cost of constructing a network object for
    every row matters at scope-list scale.  Returns 0 on a malformed
    string so a bad row never 500s the dashboard.
    """
    if not cidr or "/" not in cidr:
        return 0
    try:
        addr, prefix = cidr.rsplit("/", 1)
        prefix_n = int(prefix)
        host_bits = (128 if ":" in addr else 32) - prefix_n
        if host_bits < 0:
            return 0
        return 1 << host_bits
    except (ValueError, IndexError):
        return 0


@router.get(
    "/",
    response_model=ProjectCoverageResponse,
    summary="Project coverage summary across recon / plan / execution stages (v3 alpha.3)",
)
def get_project_coverage(
    project_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
) -> ProjectCoverageResponse:
    """Return host-coverage and scope-coverage summary for this project.

    Single endpoint, all aggregates returned together — the v3
    Operations page renders the whole "Host coverage" section from one
    call.  Counts are distinct-host counts, never row counts (a host
    with three plan entries still counts as one plan-covered host).
    """
    # --- Host-of-record total ----------------------------------------
    total_hosts = (
        db.query(func.count(Host.id))
        .filter(Host.project_id == project.id)
        .scalar()
        or 0
    )

    # --- Plan-covered hosts: distinct host_id across TestPlanEntry ----
    # Join through TestPlan implicitly via the FK on the entry; filter
    # to entries whose plan belongs to this project.  We don't need to
    # join TestPlan here because the entry's host_id is what we count
    # and the host's project_id filter handles project-scoping (entries
    # against hosts in another project shouldn't exist in the first
    # place, but the filter is defensive).
    hosts_with_plan_entry = (
        db.query(func.count(distinct(TestPlanEntry.host_id)))
        .join(Host, Host.id == TestPlanEntry.host_id)
        .filter(Host.project_id == project.id)
        .scalar()
        or 0
    )

    # --- Execution-covered hosts: distinct hosts with at least one
    # TestExecutionResult (joined through the entry).  Using the
    # result-table avoids double-counting hosts that have entries but
    # were never actually tested.
    hosts_with_execution_result = (
        db.query(func.count(distinct(TestPlanEntry.host_id)))
        .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
        .join(Host, Host.id == TestPlanEntry.host_id)
        .filter(Host.project_id == project.id)
        .scalar()
        or 0
    )

    # --- Scope-level coverage ----------------------------------------
    # One row per scope.  total_scoped_ips is the sum of CIDR sizes;
    # discovered_in_scope is the distinct host count via the existing
    # HostSubnetMapping correlation table.
    # v2.86.12 — collapsed the per-scope queries into three batched ones.
    # Pre-fix, each scope fired (1) a Subnet load + (2) a distinct host
    # count, so a project with 50 scopes did 100+ round-trips just to
    # render the coverage section.  Now: one Scope load, one Subnet load
    # across every scope (we still need the raw CIDR strings to compute
    # ``_cidr_size`` in Python), and one batched GROUP BY for distinct
    # discovered hosts keyed by scope_id.
    scopes_rows: List[ScopeCoverage] = []
    scope_objs = (
        db.query(Scope)
        .filter(Scope.project_id == project.id)
        .order_by(Scope.created_at.desc())
        .all()
    )
    scope_ids = [s.id for s in scope_objs]

    # Group subnets by scope_id so the per-scope loop below has both the
    # CIDR list (for size-sum) and the subnet_id list (for the mappings
    # join) in one bucket.
    subnets_by_scope: dict[int, List[Subnet]] = {sid: [] for sid in scope_ids}
    if scope_ids:
        for subnet in (
            db.query(Subnet)
            .filter(Subnet.scope_id.in_(scope_ids))
            .all()
        ):
            subnets_by_scope.setdefault(subnet.scope_id, []).append(subnet)

    # Distinct host counts per scope in ONE GROUP BY query — replaces the
    # previous N-scope-per-pageload pattern.  Joins through Subnet so the
    # mapping rows are keyed back to the originating scope.
    discovered_by_scope: dict[int, int] = {sid: 0 for sid in scope_ids}
    if scope_ids:
        rows = (
            db.query(
                Subnet.scope_id,
                func.count(distinct(HostSubnetMapping.host_id)),
            )
            .join(HostSubnetMapping, HostSubnetMapping.subnet_id == Subnet.id)
            .join(Host, Host.id == HostSubnetMapping.host_id)
            .filter(
                Subnet.scope_id.in_(scope_ids),
                Host.project_id == project.id,
            )
            .group_by(Subnet.scope_id)
            .all()
        )
        for scope_id, count in rows:
            discovered_by_scope[scope_id] = int(count or 0)

    for scope in scope_objs:
        subnets = subnets_by_scope.get(scope.id, [])
        total_ips = sum(_cidr_size(s.cidr) for s in subnets)
        discovered = discovered_by_scope.get(scope.id, 0)

        pct: Optional[float] = None
        if total_ips > 0:
            # Cap at 100.0 — a scope can over-deliver if there are hosts
            # that appear in multiple of its overlapping subnets and the
            # /32 count was conservative.  Showing >100% would just
            # confuse the user.
            pct = min(100.0, round(100.0 * discovered / total_ips, 2))

        scopes_rows.append(
            ScopeCoverage(
                scope_id=scope.id,
                scope_name=scope.name,
                subnet_count=len(subnets),
                total_scoped_ips=total_ips,
                discovered_in_scope=discovered,
                coverage_percent=pct,
            )
        )

    # --- Hosts outside any declared scope ----------------------------
    # A host is "outside scope" if it has no HostSubnetMapping row.
    # When the project has zero scopes declared the answer is 0 (no
    # scopes to be outside of) — this matches user expectation: "I
    # haven't declared scope, nothing is technically out-of-scope".
    if scope_objs:
        hosts_outside_scope = (
            db.query(func.count(Host.id))
            .filter(
                Host.project_id == project.id,
                ~db.query(HostSubnetMapping.id)
                .filter(HostSubnetMapping.host_id == Host.id)
                .exists(),
            )
            .scalar()
            or 0
        )
    else:
        hosts_outside_scope = 0

    return ProjectCoverageResponse(
        project_id=project.id,
        total_hosts=total_hosts,
        hosts_with_plan_entry=hosts_with_plan_entry,
        hosts_with_execution_result=hosts_with_execution_result,
        hosts_no_plan=max(0, total_hosts - hosts_with_plan_entry),
        hosts_no_execution=max(0, total_hosts - hosts_with_execution_result),
        total_scopes=len(scope_objs),
        scopes=scopes_rows,
        hosts_outside_scope=hosts_outside_scope,
    )
