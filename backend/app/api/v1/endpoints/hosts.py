"""
Hosts API — works with the deduplicated host schema.

v2.27.0 — query construction and serialization extracted to
``app/services/host_query.py`` and ``app/services/host_serialization.py``.
This file is now focused on HTTP concerns: routing, auth, response
envelope assembly.
"""

import logging
from pathlib import Path
from typing import Any, List, Optional, Dict
from datetime import datetime
import ipaddress
import json

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, selectinload, aliased
from sqlalchemy import or_, and_, distinct, func, select, true
from sqlalchemy.sql import exists

from app.db.session import get_db
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project
from app.db.models_auth import User, UserRole
from app.db import models
from app.db.models_confidence import HostConfidence, PortConfidence, ConflictHistory, NetexecResult
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_agent import TestPlanEntry, TestPlan, TestExecutionResult
from app.services.host_serialization import _serialize_follow, _serialize_note  # CR4-2
from app.schemas.schemas import (
    Host as HostSchema,
    HostListResponse,
    HostVulnerabilitySummary,
    HostFollowInfo,
    Annotation,
    AnnotationCreate,
    AnnotationUpdate,
    HostFollowUpdate,
)
from pydantic import BaseModel, ConfigDict, Field
from app.services.vulnerability_service import VulnerabilityService
from app.services.host_follow_service import HostFollowService
from app.services.host_query import (
    SERVICE_PORT_MAPPINGS,
    escape_like as _escape_like,
    parse_subnets as _parse_subnets,
    make_correlated_subquery as _make_correlated_subquery,
    build_filtered_host_query as _build_filtered_host_query,
    apply_host_sorting as _apply_host_sorting,
)
from app.services.host_serialization import (
    SEVERITY_ORDER,
    LIST_DISCOVERY_CAP,
    discovery_dict as _discovery_dict,
    build_vuln_summary as _build_vuln_summary,
    serialize_host_base as _serialize_host_base,
    serialize_host_detail as _serialize_host_detail,
    serialize_port_light as _serialize_port_light,
    serialize_vulnerability as _serialize_vulnerability,
    vulnerability_sort_key as _vulnerability_sort_key,
)
from app.db.models import HostFollow, FollowStatus, Annotation as AnnotationModel, NoteStatus


# --- Response schemas for previously untyped endpoints ---

class PortFilterItem(BaseModel):
    port: int
    service: str = "unknown"
    state: Optional[str] = None
    count: int = 0

class ServiceFilterItem(BaseModel):
    name: str
    count: int = 0

class OsFilterItem(BaseModel):
    name: str
    count: int = 0

class SubnetFilterItem(BaseModel):
    cidr: str
    scope_name: Optional[str] = None
    host_count: int = 0

class ScanFilterItem(BaseModel):
    id: int
    filename: Optional[str] = None
    tool_name: Optional[str] = None
    created_at: Optional[str] = None
    start_time: Optional[str] = None

class TechnologyFilterItem(BaseModel):
    name: str
    host_count: int


class TagFilterItem(BaseModel):
    id: int
    name: str
    color: Optional[str] = None
    host_count: int = 0


class SubnetLabelFilterItem(BaseModel):
    """A project subnet label as it appears on the host filter combobox (v2.86.0)."""
    id: int
    name: str
    color: Optional[str] = None
    # COUNT(DISTINCT host_id) reachable via subnets carrying this label.
    # Must be distinct: a host can sit in multiple labeled subnets and a
    # naive assignment-row count would double it.
    host_count: int = 0


class HostFilterDataResponse(BaseModel):
    common_ports: List[PortFilterItem]
    services: List[ServiceFilterItem]
    operating_systems: List[OsFilterItem]
    subnets: List[SubnetFilterItem]
    scans: List[ScanFilterItem]
    # v2.12.1: distinct web-fingerprint tech strings seen on in-scope
    # hosts, with host counts for the HostFilters autocomplete.
    technologies: List[TechnologyFilterItem] = []
    # v2.71.0: project tags with host counts for the tag filter combobox.
    tags: List[TagFilterItem] = []
    # v2.86.0: subnet labels with distinct-host counts.
    subnet_labels: List[SubnetLabelFilterItem] = []

class ConfidenceEntry(BaseModel):
    id: int
    field_name: str
    confidence_score: Optional[float] = None
    scan_type: Optional[str] = None
    data_source: Optional[str] = None
    method: Optional[str] = None
    scan_id: Optional[int] = None
    updated_at: Optional[str] = None
    additional_factors: Optional[dict] = None
    object_type: str
    port_id: Optional[int] = None

class ConflictEntry(BaseModel):
    id: int
    object_type: str
    object_id: Optional[int] = None
    field_name: Optional[str] = None
    previous_value: Optional[str] = None
    previous_confidence: Optional[float] = None
    previous_scan_id: Optional[int] = None
    previous_method: Optional[str] = None
    new_value: Optional[str] = None
    new_confidence: Optional[float] = None
    new_scan_id: Optional[int] = None
    new_method: Optional[str] = None
    resolved_at: Optional[str] = None

class HostConflictsResponse(BaseModel):
    confidence: List[ConfidenceEntry]
    conflict_history: List[ConflictEntry]


# v2.90.0 (#44.1 follow-through, UX phase 3) — per-host DNS records
# surface.  ``resolver_name`` is the v2.89.0 column; the response
# includes the distinct resolver list + record-type list as
# convenience aggregates so the frontend card can render summary
# pills without iterating the full result set.
class HostDnsRecordRow(BaseModel):
    id: int
    domain: str
    record_type: str
    value: str
    ttl: Optional[int] = None
    resolver_name: Optional[str] = None
    created_at: datetime


class HostDnsRecordsResponse(BaseModel):
    items: List[HostDnsRecordRow]
    total: int
    resolvers: List[str]
    record_types: List[str]


router = APIRouter(dependencies=[Depends(get_current_user)])


class HostFilterParams:
    """The shared /hosts filter query params, declared once.

    Consumed via ``Depends()`` by every endpoint that filters hosts
    (listing, matching-ids, filter-data, tool-ready export).  Declaring
    the params here — instead of repeating ~25 ``Query(...)`` defaults
    across four signatures — means adding a filter dimension is a one-line
    change in one place, and the four endpoints can never drift out of
    sync.  Attribute names match ``build_filtered_host_query``'s kwargs,
    so ``as_builder_kwargs()`` splats straight in.
    """

    def __init__(
        self,
        state: Optional[str] = Query(None, description="Host state filter", examples=["up"]),
        search: Optional[str] = Query(None, description="Search by IP address, hostname, OS name, port number, or service name", examples=["10.0.0"]),
        ports: Optional[str] = Query(None, description="Comma-separated port numbers to match", examples=["22,80,443,8080"]),
        services: Optional[str] = Query(None, description="Comma-separated service names to match (mapped to common ports automatically)", examples=["ssh,http,https,rdp"]),
        port_states: Optional[str] = Query(None, description="Comma-separated port states to match", examples=["open,filtered"]),
        has_open_ports: Optional[bool] = Query(None, description="If true, only hosts with at least one open port"),
        os_filter: Optional[str] = Query(None, description="Filter by OS name or family (partial match)", examples=["Linux"]),
        subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks; hosts must fall within at least one", examples=["192.168.1.0/24,10.0.0.0/8"]),
        has_critical_vulns: Optional[bool] = Query(None, description="If true, only hosts with critical-severity vulnerabilities"),
        has_high_vulns: Optional[bool] = Query(None, description="If true, only hosts with high-severity vulnerabilities"),
        has_medium_vulns: Optional[bool] = Query(None, description="If true, only hosts with medium-severity vulnerabilities"),
        has_low_vulns: Optional[bool] = Query(None, description="If true, only hosts with low-severity vulnerabilities"),
        has_exploit_available: Optional[bool] = Query(None, description="If true, only hosts with at least one vulnerability flagged as exploitable by Nessus (exploit_available / metasploit_name / canvas_package / core_impact_name / exploit_code_maturity in {functional, high, proof-of-concept})"),
        has_test_execution: Optional[bool] = Query(None, description="If true, only hosts that have had at least one agentic test executed against them (i.e. at least one TestExecutionResult row recorded via any TestPlanEntry for the host). Drives the 'tested' badge on the Hosts list."),
        follow_status: Optional[str] = Query(None, description="Filter by review status: watching, in_review, reviewed, or none", examples=["watching"]),
        out_of_scope_only: Optional[bool] = Query(None, description="If true, only hosts not mapped to any scope/subnet"),
        scan_ids: Optional[str] = Query(None, description="Comma-separated scan IDs; hosts must appear in at least one", examples=["1,2,5"]),
        first_seen_in_scan: Optional[bool] = Query(None, description="Used with scan_ids — if true, only hosts first discovered in those scans"),
        with_notes_only: Optional[bool] = Query(None, description="If true, only hosts that have at least one note"),
        has_web_interface: Optional[bool] = Query(None, description="If true, only hosts with at least one web interface recorded (httpx / eyewitness / nikto)"),
        tech: Optional[str] = Query(None, description="Comma-separated list of technology strings; OR semantics — host qualifies if any interface has any listed tech (substring match, case-insensitive)", examples=["nginx,jenkins"]),
        tags: Optional[str] = Query(None, description="Comma-separated tag IDs; OR semantics — host qualifies if it carries any listed tag", examples=["3,7"]),
        subnet_labels: Optional[str] = Query(None, description="Comma-separated subnet-label IDs; OR semantics — host qualifies if it sits in any subnet carrying any listed label", examples=["2,5"]),
        assigned_to: Optional[str] = Query(None, description="Assignment filter: 'me', 'any', or a numeric user id", examples=["me"]),
        q: Optional[str] = Query(None, description="Boolean query DSL. Fields (port, os, service, subnet, tag, label, cve, vuln, header, note, has:, …) combined with AND/OR/NOT + parentheses. Comma = OR within a field; repeated field = AND. e.g. 'port:80 port:443 AND NOT tag:test', 'cve:CVE-2021-44228 OR vuln:\"log4j\"'. ANDs with the other filters."),
    ):
        self.state = state
        self.search = search
        self.ports = ports
        self.services = services
        self.port_states = port_states
        self.has_open_ports = has_open_ports
        self.os_filter = os_filter
        self.subnets = subnets
        self.has_critical_vulns = has_critical_vulns
        self.has_high_vulns = has_high_vulns
        self.has_medium_vulns = has_medium_vulns
        self.has_low_vulns = has_low_vulns
        self.has_exploit_available = has_exploit_available
        self.has_test_execution = has_test_execution
        self.follow_status = follow_status
        self.out_of_scope_only = out_of_scope_only
        self.scan_ids = scan_ids
        self.first_seen_in_scan = first_seen_in_scan
        self.with_notes_only = with_notes_only
        self.has_web_interface = has_web_interface
        self.tech = tech
        self.tags = tags
        self.subnet_labels = subnet_labels
        self.assigned_to = assigned_to
        self.q = q

    def as_builder_kwargs(self) -> Dict[str, Any]:
        """Kwargs for ``build_filtered_host_query`` (excludes ``project_id``,
        which the endpoint supplies from the resolved project)."""
        return dict(self.__dict__)

    def active(self) -> bool:
        """True if any filter (including ``q``) is set — used by the
        filter-data endpoint to decide whether to scope the cascade."""
        return any(v is not None for v in self.__dict__.values())


@router.get("/", response_model=HostListResponse)
def get_hosts_v2(
    filters: HostFilterParams = Depends(),
    skip: int = Query(0, ge=0),
    # v2.86.4 — was bare ``int = 100`` with no upper bound; ``limit=50_000``
    # could pin a worker because every row fans out selectinload of ports,
    # scripts, notes, scan_history, tags + page-wide aggregations.  Cap at
    # 500 to bound the worst case while staying generous for normal use.
    limit: int = Query(100, ge=1, le=500),
    include_total: bool = Query(True, description="Include the total number of matching hosts"),
    sort_by: str = Query("critical_vulns", pattern="^(critical_vulns|high_vulns|open_ports|note_count|discovery_count|ip_address|hostname|last_seen)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get hosts from v2 schema (deduplicated by IP)."""
    query = _build_filtered_host_query(
        db, current_user,
        **filters.as_builder_kwargs(),
        project_id=project.id,
    )

    if include_total:
        total = query.with_entities(func.count(models.Host.id)).scalar()
    else:
        total = None
    query = _apply_host_sorting(query, sort_by, sort_order)

    # Add eager loading for the listing response.  RV-8 — the list view
    # never renders NSE script bodies, so we deliberately DON'T eager-load
    # Port.scripts or Host.host_scripts here; the list serializer emits
    # script-free ports (serialize_port_light) and never touches those
    # relationships.  Review #5 — we ALSO don't eager-load notes or
    # scan_history (a host can have thousands of rows); the list only shows
    # the 3 newest notes + 6 newest discoveries, fetched with bounded
    # window queries below, plus aggregate counts.  Detail loads everything.
    query = query.options(
        selectinload(models.Host.ports),
        # Eager-load tag + its definition so serialize_host_base reads
        # host.tag_assignments[].tag without an N+1 per host.
        selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
    )

    # Apply pagination and return
    hosts = query.offset(skip).limit(limit).all()
    host_ids = [host.id for host in hosts]

    # Review #5 — bounded per-host slices via window queries instead of
    # materialising every child row.  Aggregate note counts; top-3 notes;
    # top-(cap) distinct-scan discoveries.
    note_count_map: Dict[int, int] = {}
    notes_by_host: Dict[int, list] = {}
    discoveries_by_host: Dict[int, list] = {}
    if host_ids:
        note_count_map = dict(
            db.query(models.Annotation.host_id, func.count(models.Annotation.id))
            .filter(models.Annotation.host_id.in_(host_ids))
            .group_by(models.Annotation.host_id)
            .all()
        )
        note_rn = func.row_number().over(
            partition_by=models.Annotation.host_id,
            order_by=(models.Annotation.created_at.desc(), models.Annotation.id.desc()),
        ).label("rn")
        ranked_notes = (
            db.query(models.Annotation.id.label("nid"), note_rn)
            .filter(models.Annotation.host_id.in_(host_ids))
            .subquery()
        )
        top_note_ids = [
            r.nid for r in db.query(ranked_notes.c.nid).filter(ranked_notes.c.rn <= 3).all()
        ]
        if top_note_ids:
            for n in (
                db.query(models.Annotation)
                .filter(models.Annotation.id.in_(top_note_ids))
                .options(
                    selectinload(models.Annotation.author),
                    selectinload(models.Annotation.assignee),
                    selectinload(models.Annotation.promoted_findings),
                )
                .all()
            ):
                notes_by_host.setdefault(n.host_id, []).append(n)
            for arr in notes_by_host.values():
                arr.sort(key=lambda n: (n.created_at or n.updated_at or datetime.min), reverse=True)

        # Discoveries: window the newest history rows per host (over-fetch a
        # little so distinct-scan dedupe still yields up to the cap), join scan.
        disc_rn = func.row_number().over(
            partition_by=models.HostScanHistory.host_id,
            order_by=(models.HostScanHistory.discovered_at.desc(), models.HostScanHistory.id.desc()),
        ).label("rn")
        ranked_hist = (
            db.query(models.HostScanHistory.id.label("hid"), disc_rn)
            .filter(models.HostScanHistory.host_id.in_(host_ids))
            .subquery()
        )
        top_hist_ids = [
            r.hid for r in db.query(ranked_hist.c.hid)
            .filter(ranked_hist.c.rn <= LIST_DISCOVERY_CAP * 2).all()
        ]
        if top_hist_ids:
            hist_rows = (
                db.query(models.HostScanHistory)
                .filter(models.HostScanHistory.id.in_(top_hist_ids))
                .options(selectinload(models.HostScanHistory.scan))
                .all()
            )
            hist_rows.sort(key=lambda h: (h.discovered_at or datetime.min), reverse=True)
            seen_by_host: Dict[int, set] = {}
            for h in hist_rows:
                seen = seen_by_host.setdefault(h.host_id, set())
                bucket = discoveries_by_host.setdefault(h.host_id, [])
                if h.scan_id in seen or len(bucket) >= LIST_DISCOVERY_CAP:
                    continue
                seen.add(h.scan_id)
                bucket.append(_discovery_dict(h))

    vuln_error = False
    try:
        vulnerability_service = VulnerabilityService(db)
        vuln_map = vulnerability_service.get_bulk_host_vulnerability_summaries(host_ids)
    except Exception:
        logger.exception("Failed to load vulnerability summaries for %d hosts", len(host_ids))
        vuln_map = {hid: {'total': 0, 'by_severity': {}} for hid in host_ids}
        vuln_error = True

    follow_records = []
    if host_ids:
        follow_records = (
            db.query(HostFollow)
            .filter(HostFollow.user_id == current_user.id, HostFollow.host_id.in_(host_ids))
            .all()
        )
    follow_map = {record.host_id: record for record in follow_records}

    # Batch lookup: test plan entry counts per host.  Mirrors the host
    # detail page filter — count entries from plans the team has
    # accepted (approved/in_progress/completed) and exclude entries
    # that have been explicitly rejected by a tester.  proposed entries
    # in an approved plan still count: post-approval, "proposed" means
    # "queued for execution", not "untriaged".
    tp_count_map: Dict[int, int] = {}
    if host_ids:
        tp_rows = (
            db.query(TestPlanEntry.host_id, func.count(TestPlanEntry.id))
            .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
            .filter(
                TestPlanEntry.host_id.in_(host_ids),
                TestPlan.project_id == project.id,
                TestPlan.status.in_(("approved", "in_progress", "completed")),
                TestPlanEntry.status != "rejected",
            )
            .group_by(TestPlanEntry.host_id)
            .all()
        )
        tp_count_map = {row[0]: row[1] for row in tp_rows}

    # Batch lookup: TestExecutionResult counts per host (v2.81.0).
    # Joins TestPlanEntry by entry_id to land on host_id, then counts
    # the rows.  Drives the "tested" left-border accent on the Hosts
    # list — a host with count>0 has had at least one agentic test
    # executed against it (distinct from tp_count_map which only
    # counts whether the host is in a plan).  One grouped query per
    # page, not N+1.
    te_count_map: Dict[int, int] = {}
    if host_ids:
        te_rows = (
            db.query(TestPlanEntry.host_id, func.count(TestExecutionResult.id))
            .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
            .filter(TestPlanEntry.host_id.in_(host_ids))
            .group_by(TestPlanEntry.host_id)
            .all()
        )
        te_count_map = {row[0]: row[1] for row in te_rows}

    # Batch lookup: web interface counts per host (v2.12.0).
    # Drives the "Web" badge on the Hosts list (phase 2 UI) and
    # feeds the per-host HostDetail card count.
    wi_count_map: Dict[int, int] = {}
    if host_ids:
        wi_rows = (
            db.query(models.WebInterface.host_id, func.count(models.WebInterface.id))
            .filter(models.WebInterface.host_id.in_(host_ids))
            .group_by(models.WebInterface.host_id)
            .all()
        )
        wi_count_map = {row[0]: row[1] for row in wi_rows}

    # Batch lookup: OTHER users' In-Review follows per host (v4.9.1).  Drives
    # the "In review · <name>" indicator on the Hosts list so an operator sees
    # a teammate is already on a host before picking it up.  One query for the
    # whole page — not N+1.
    #
    # EXCLUDES the caller (`user_id != current_user.id`) ON PURPOSE: the
    # caller's own in-review status is already shown by the row's interactive
    # Follow control ("In Review").  Including the caller here produced a
    # DUPLICATE — the Follow control's "In Review" AND a second "In review ·
    # <you>" badge.  No self-inclusion avoids that dup, so the named badge is
    # teammates-only; your own status lives on the Follow control.  (Briefly
    # included self in a prior pass; reverted — do NOT re-add without first
    # removing the Follow-control status display.)
    other_review_map: Dict[int, list] = {}
    if host_ids:
        review_rows = (
            db.query(HostFollow.host_id, HostFollow.user_id, User.username, User.full_name)
            .join(User, HostFollow.user_id == User.id)
            .filter(
                HostFollow.host_id.in_(host_ids),
                HostFollow.user_id != current_user.id,
                HostFollow.status == FollowStatus.IN_REVIEW.value,
            )
            .all()
        )
        for hid, uid, username, full_name in review_rows:
            other_review_map.setdefault(hid, []).append({
                "user_id": uid,
                "name": full_name or username,
            })

    # Batch lookup: assignees per host (v2.71.0).  A follow row with a
    # non-null assigned_at means "host assigned to user_id".  One query
    # for the page — drives the Hosts-list assignee badge.
    assignee_map: Dict[int, list] = {}
    if host_ids:
        assignee_rows = (
            db.query(
                HostFollow.host_id,
                HostFollow.user_id,
                HostFollow.assigned_at,
                HostFollow.assigned_by_id,
                User.username,
                User.full_name,
            )
            .join(User, HostFollow.user_id == User.id)
            .filter(
                HostFollow.host_id.in_(host_ids),
                HostFollow.assigned_at.isnot(None),
            )
            .all()
        )
        for hid, uid, assigned_at, assigned_by_id, username, full_name in assignee_rows:
            assignee_map.setdefault(hid, []).append({
                "user_id": uid,
                "name": full_name or username,
                "assigned_at": assigned_at,
                "assigned_by_id": assigned_by_id,
            })

    # Batch lookup: count of ACTIVE findings per host (foundation 6d).  Drives
    # the Hosts-list finding badge so triage state is visible without opening
    # each host.  "Active" = not yet closed (excludes false_positive /
    # accepted_risk / remediated).  One grouped query for the page — not N+1.
    finding_count_map: Dict[int, int] = {}
    if host_ids:
        from app.db.models_findings import Finding, FindingHost, FindingStatus
        _active = [
            FindingStatus.OPEN.value, FindingStatus.CONFIRMED.value, FindingStatus.RETEST.value,
        ]
        for hid, cnt in (
            db.query(FindingHost.host_id, func.count(func.distinct(Finding.id)))
            .join(Finding, Finding.id == FindingHost.finding_id)
            .filter(FindingHost.host_id.in_(host_ids), Finding.status.in_(_active))
            .group_by(FindingHost.host_id)
            .all()
        ):
            finding_count_map[hid] = cnt

    serialized_hosts = []
    for host in hosts:
        # Review #5 — pass the windowed discoveries + aggregate note_count so
        # the base serializer never touches the (unloaded) notes/scan_history
        # relationships.
        serialized = _serialize_host_base(
            host, vuln_map.get(host.id),
            discoveries=discoveries_by_host.get(host.id, []),
            note_count=note_count_map.get(host.id, 0),
        )
        follow = follow_map.get(host.id)
        serialized["follow"] = _serialize_follow(follow) if follow else None

        # RV-8 — list-weight payload: script-free ports, no host_scripts.
        serialized["ports"] = [_serialize_port_light(p) for p in host.ports]
        serialized["host_scripts"] = []

        serialized["test_plan_entry_count"] = tp_count_map.get(host.id, 0)
        serialized["test_execution_count"] = te_count_map.get(host.id, 0)
        serialized["web_interface_count"] = wi_count_map.get(host.id, 0)
        serialized["finding_count"] = finding_count_map.get(host.id, 0)
        serialized["other_reviewers"] = other_review_map.get(host.id, [])
        serialized["assignees"] = assignee_map.get(host.id, [])
        serialized["notes"] = [
            _serialize_note(note) for note in notes_by_host.get(host.id, [])
        ]
        serialized_hosts.append(serialized)

    return {
        "items": serialized_hosts,
        "total": total,
        "skip": skip,
        "limit": limit,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "vulnerability_error": vuln_error,
    }


class HostIdsResponse(BaseModel):
    ids: List[int]
    total: int
    capped: bool = False


# Upper bound on ids returned for a bulk "select all matching" — keeps the
# response (and any follow-up bulk mutation) bounded.  Mirrored as the
# per-call cap in the bulk endpoints.
_BULK_SELECT_CAP = 5000


@router.get(
    "/ids",
    response_model=HostIdsResponse,
    summary="Matching host IDs for the current filters (bulk select-all)",
)
def get_matching_host_ids(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Return just the host IDs matching the current filters, capped.

    Backs the Hosts-page "select all matching" bulk affordance: the
    client sends the same filter params it uses for the list, gets back
    the full id set (up to the cap), then hands those ids to a bulk
    endpoint.  Returns only ids — no per-host payload — so it stays cheap
    even for large result sets.
    """
    query = _build_filtered_host_query(
        db, current_user,
        **filters.as_builder_kwargs(),
        project_id=project.id,
    )
    total = query.with_entities(func.count(models.Host.id)).scalar() or 0
    rows = query.with_entities(models.Host.id).limit(_BULK_SELECT_CAP).all()
    ids = [r[0] for r in rows]
    return HostIdsResponse(ids=ids, total=total, capped=total > len(ids))


@router.get(
    "/filters/data",
    response_model=HostFilterDataResponse,
    summary="Get host filter options",
)
def get_host_filter_data_v2(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get available filter data, optionally scoped to the current filter context (cascading filters)."""

    has_filters = filters.active()

    # Build a subquery of matching host IDs when filters are active
    if has_filters:
        filtered_ids = _build_filtered_host_query(
            db, current_user,
            **filters.as_builder_kwargs(),
            project_id=project.id,
        ).with_entities(models.Host.id).scalar_subquery()
        host_scope = models.Host.id.in_(filtered_ids)
    else:
        host_scope = models.Host.project_id == project.id

    # Ports — scoped to filtered hosts when active
    port_query = db.query(
        models.Port.port_number,
        models.Port.service_name,
        models.Port.state,
        func.count(models.Port.id).label('count')
    )
    if host_scope is not None:
        port_query = port_query.join(models.Host).filter(host_scope)
    common_ports = port_query.group_by(
        models.Port.port_number, models.Port.service_name, models.Port.state
    ).order_by(func.count(models.Port.id).desc()).limit(500).all()

    # Services — scoped
    svc_query = db.query(
        models.Port.service_name,
        func.count(models.Port.id).label('count')
    ).filter(models.Port.service_name.isnot(None), models.Port.service_name != '')
    if host_scope is not None:
        svc_query = svc_query.join(models.Host).filter(host_scope)
    services_result = svc_query.group_by(
        models.Port.service_name
    ).order_by(func.count(models.Port.id).desc()).limit(200).all()

    # Operating systems — scoped
    os_query = db.query(
        models.Host.os_name,
        func.count(models.Host.id).label('count')
    ).filter(models.Host.os_name.isnot(None), models.Host.os_name != '')
    if host_scope is not None:
        os_query = os_query.filter(host_scope)
    operating_systems = os_query.group_by(
        models.Host.os_name
    ).order_by(func.count(models.Host.id).desc()).limit(100).all()

    # Technologies — v2.12.1.  Pulls the flattened tech strings from
    # all web_interfaces in scope, counts host-level uniqueness, and
    # returns the top 200 sorted by frequency for the HostFilters
    # autocomplete.  One tech string can appear on multiple hosts;
    # we count distinct hosts per tech, not distinct interfaces.
    #
    # v2.86.5 — pushed the per-(host, tech-array-element) unnest +
    # COUNT(DISTINCT host_id) GROUP BY into Postgres via
    # ``json_array_elements_text``.  Pre-fix the endpoint loaded every
    # WebInterface row (host_id + technologies JSON array) and iterated
    # in Python — for projects with thousands of fingerprinted services
    # that was the dominant cost on every /hosts page entry.  On SQLite
    # (used by the test suite when Postgres isn't reachable) the
    # ``json_array_elements_text`` function doesn't exist, so we fall
    # back to the previous Python aggregation there.
    dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
    if dialect == "postgresql":
        tech_unnest = func.json_array_elements_text(models.WebInterface.technologies).table_valued("name")
        tech_q = (
            db.query(
                tech_unnest.c.name.label("name"),
                func.count(func.distinct(models.WebInterface.host_id)).label("host_count"),
            )
            .select_from(models.WebInterface)
            .join(tech_unnest, true())
            .filter(models.WebInterface.technologies.isnot(None))
            # SQLAlchemy stores Python ``None`` as the JSON literal
            # ``null`` (not SQL NULL), and ``json_array_elements_text``
            # rejects scalars with "cannot call ... on a scalar".  Also
            # guard against legacy rows where the JSON value is an object
            # or string by accident.  ``json_typeof`` returns 'array' for
            # the well-formed case.
            .filter(func.json_typeof(models.WebInterface.technologies) == "array")
        )
        if host_scope is not None:
            tech_q = tech_q.join(
                models.Host, models.Host.id == models.WebInterface.host_id
            ).filter(host_scope)
        else:
            tech_q = tech_q.filter(models.WebInterface.project_id == project.id)
        tech_q = (
            tech_q.group_by(tech_unnest.c.name)
            .order_by(func.count(func.distinct(models.WebInterface.host_id)).desc(), tech_unnest.c.name.asc())
            .limit(200)
        )
        technologies_result = [
            {"name": name, "host_count": int(host_count or 0)}
            for name, host_count in tech_q.all()
            if name  # filter empty/None tech strings the parser sometimes leaves behind
        ]
    else:
        # SQLite fallback — preserves pre-v2.86.5 Python aggregation.
        tech_query = (
            db.query(
                models.WebInterface.host_id,
                models.WebInterface.technologies,
            )
            .filter(models.WebInterface.technologies.isnot(None))
        )
        if host_scope is not None:
            tech_query = tech_query.join(
                models.Host, models.Host.id == models.WebInterface.host_id
            ).filter(host_scope)
        else:
            tech_query = tech_query.filter(models.WebInterface.project_id == project.id)
        tech_host_pairs = tech_query.all()
        tech_host_sets: Dict[str, set] = {}
        for host_id, tech_list in tech_host_pairs:
            if not tech_list:
                continue
            for t in tech_list:
                if not t:
                    continue
                tech_host_sets.setdefault(str(t), set()).add(host_id)
        technologies_result = sorted(
            ({"name": name, "host_count": len(hosts)} for name, hosts in tech_host_sets.items()),
            key=lambda x: (-x["host_count"], x["name"].lower()),
        )[:200]

    # Scans — scoped to project so analysts see project-relevant scans
    scans = db.query(
        models.Scan.id, models.Scan.filename, models.Scan.tool_name,
        models.Scan.created_at, models.Scan.start_time
    ).filter(models.Scan.project_id == project.id).order_by(models.Scan.created_at.desc()).limit(100).all()

    # Tags — project-scoped definitions + assignment counts (v2.71.0).
    # Not cascaded by the active filter: the tag picker should always
    # offer every project tag, not just those on the current result set.
    tag_rows = (
        db.query(
            models.HostTag.id,
            models.HostTag.name,
            models.HostTag.color,
            func.count(models.HostTagAssignment.id),
        )
        .outerjoin(models.HostTagAssignment, models.HostTagAssignment.tag_id == models.HostTag.id)
        .filter(models.HostTag.project_id == project.id)
        .group_by(models.HostTag.id, models.HostTag.name, models.HostTag.color)
        .order_by(models.HostTag.name)
        .all()
    )
    tags_result = [
        {'id': t[0], 'name': t[1], 'color': t[2], 'host_count': t[3] or 0}
        for t in tag_rows
    ]

    # Subnet labels — project-scoped definitions + DISTINCT host counts
    # (v2.86.0).  Mirrors the tag block above with two key differences:
    # (1) counts walk through HostSubnetMapping (subnet → host is N:M
    # because a host may match multiple subnets); (2) must COUNT DISTINCT
    # host_id, since a host in two labeled subnets would otherwise be
    # counted twice.  Like tags, not cascaded by the active filter — the
    # picker always offers every project label.
    subnet_label_rows = (
        db.query(
            models.SubnetLabel.id,
            models.SubnetLabel.name,
            models.SubnetLabel.color,
            func.count(func.distinct(models.HostSubnetMapping.host_id)),
        )
        .outerjoin(
            models.SubnetLabelAssignment,
            models.SubnetLabelAssignment.label_id == models.SubnetLabel.id,
        )
        .outerjoin(
            models.HostSubnetMapping,
            models.HostSubnetMapping.subnet_id == models.SubnetLabelAssignment.subnet_id,
        )
        .filter(models.SubnetLabel.project_id == project.id)
        .group_by(models.SubnetLabel.id, models.SubnetLabel.name, models.SubnetLabel.color)
        .order_by(models.SubnetLabel.name)
        .all()
    )
    subnet_labels_result = [
        {'id': r[0], 'name': r[1], 'color': r[2], 'host_count': r[3] or 0}
        for r in subnet_label_rows
    ]

    # Subnets — scoped host counts when filters are active
    subnet_query = db.query(
        models.Subnet.cidr,
        models.Scope.name.label('scope_name'),
        func.count(models.HostSubnetMapping.id).label('host_count')
    ).join(
        models.Scope, models.Subnet.scope_id == models.Scope.id
    ).outerjoin(
        models.HostSubnetMapping, models.Subnet.id == models.HostSubnetMapping.subnet_id
    )
    if host_scope is not None:
        subnet_query = subnet_query.outerjoin(
            models.Host, models.HostSubnetMapping.host_id == models.Host.id
        ).filter(or_(host_scope, models.HostSubnetMapping.host_id.is_(None)))
    subnets_result = subnet_query.group_by(
        models.Subnet.id, models.Subnet.cidr, models.Scope.name
    ).order_by(func.count(models.HostSubnetMapping.id).desc()).limit(200).all()

    return {
        'common_ports': [
            {'port': p.port_number, 'service': p.service_name or 'unknown', 'state': p.state, 'count': p.count}
            for p in common_ports
        ],
        'services': [
            {'name': s.service_name, 'count': s.count}
            for s in services_result
        ],
        'operating_systems': [
            {'name': o.os_name, 'count': o.count}
            for o in operating_systems
        ],
        'subnets': [
            {'cidr': s.cidr, 'scope_name': s.scope_name, 'host_count': s.host_count or 0}
            for s in subnets_result
        ],
        'scans': [
            {
                'id': s.id, 'filename': s.filename, 'tool_name': s.tool_name,
                'created_at': s.created_at.isoformat() if s.created_at else None,
                'start_time': s.start_time.isoformat() if s.start_time else None,
            }
            for s in scans
        ],
        'technologies': technologies_result,
        'tags': tags_result,
        'subnet_labels': subnet_labels_result,
    }


@router.get("/scan/{scan_id}", response_model=List[HostSchema])
def get_hosts_by_scan_v2(
    scan_id: int,
    state: Optional[str] = None,
    # v2.86.9 — search + port filter pushed server-side so the
    # ScanDetail "sample hosts" table doesn't need to fetch the whole
    # 5000-row cap when the operator only wants to see one host.
    search: Optional[str] = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match on IP / hostname / OS name "
            "(v2.86.9).  LIKE-meta-character escaped at the boundary."
        ),
    ),
    port: Optional[int] = Query(
        None,
        ge=0,
        le=65535,
        description=(
            "Filter to hosts that have at least one Port row matching "
            "this port number on this scan (v2.86.9)."
        ),
    ),
    skip: int = Query(0, ge=0),
    # Hard upper bound — without it a caller can request limit=10_000_000
    # and pin a worker materializing every host (eager-loads pull ports +
    # scripts + scan history per row).  5000 fits comfortably in one
    # response; for larger pages, paginate with skip.
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """
    Get hosts that were discovered in a specific scan.
    Uses HostScanHistory to find hosts associated with the scan.
    """
    # Check if scan exists and belongs to project
    scan = db.query(models.Scan).filter(models.Scan.id == scan_id, models.Scan.project_id == project.id).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    # Query hosts through HostScanHistory
    query = db.query(models.Host).options(
        selectinload(models.Host.ports).selectinload(models.Port.scripts),
        selectinload(models.Host.host_scripts),
        selectinload(models.Host.notes).selectinload(models.Annotation.author),
        # review #8a — _serialize_note reads note.assignee; eager-load it so
        # the notes slice doesn't trigger an N+1 over assignees.
        selectinload(models.Host.notes).selectinload(models.Annotation.assignee),
        # _serialize_note reads note.promoted_findings for the "promoted" badge.
        selectinload(models.Host.notes).selectinload(models.Annotation.promoted_findings),
        selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan),
    ).join(
        models.HostScanHistory, models.Host.id == models.HostScanHistory.host_id
    ).filter(
        models.HostScanHistory.scan_id == scan_id
    )

    # Apply state filter if provided
    if state:
        query = query.filter(models.Host.state == state)

    # v2.86.9 — server-side search across IP / hostname / OS.
    if search:
        escaped = _escape_like(search)
        like = f"%{escaped}%"
        query = query.filter(
            or_(
                models.Host.ip_address.ilike(like),
                models.Host.hostname.ilike(like),
                models.Host.os_name.ilike(like),
            )
        )

    # v2.86.9 — port filter.  Subquery returns host_ids that have at
    # least one Port row matching ``port`` (no state filter — operators
    # often want to see closed/filtered too while debugging coverage).
    if port is not None:
        port_host_ids = (
            db.query(models.Port.host_id)
            .filter(models.Port.port_number == port)
            .distinct()
        )
        query = query.filter(models.Host.id.in_(port_host_ids))

    # Apply pagination and return
    hosts = query.offset(skip).limit(limit).all()
    return hosts


@router.get("/{host_id:int}", response_model=HostSchema)
def get_host_v2(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get a specific host by ID with vulnerability information.

    The `:int` Starlette path converter is load-bearing: without it,
    this route's pattern (default str converter) catches sibling
    static routes like `/views` that are registered later in the file.
    Starlette routes match in registration order, and on str-converter
    matches FastAPI runs parameter validation which raises 422 instead
    of falling through to the next route.  Pinning the converter to
    `int` makes Starlette skip this route entirely for non-int paths.
    """
    host = db.query(models.Host).options(
        selectinload(models.Host.ports).selectinload(models.Port.scripts),
        selectinload(models.Host.host_scripts),
        selectinload(models.Host.vulnerabilities).selectinload(Vulnerability.port),
        # serialize_vulnerability reads vuln.promoted_findings for the "Promoted" badge.
        selectinload(models.Host.vulnerabilities).selectinload(Vulnerability.promoted_findings),
        selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan)
    ).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()

    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    follow_service = HostFollowService(db)

    try:
        vulnerability_service = VulnerabilityService(db)
        # Count-only summary: the detail serializer reads only total +
        # by_severity from this (via build_vuln_summary) and builds the
        # actual vulnerability list separately from host.vulnerabilities.
        # get_host_vulnerability_summary would serialize every vuln row to
        # a dict that's then discarded — double work on Nessus-heavy hosts.
        vuln_summary = vulnerability_service.get_bulk_host_vulnerability_summaries(
            [host_id]
        ).get(host_id, {"total": 0, "by_severity": {}})
    except Exception:
        logger.exception("Failed to load vulnerability summary for host %d", host_id)
        vuln_summary = {
            'total': 0,
            'by_severity': {},
            'error': True,
        }

    follow_record = follow_service.get_follow(host_id, current_user.id)
    notes = follow_service.list_notes(host_id)

    serialized = _serialize_host_detail(host, vuln_summary, follow_record, notes)
    # v2.12.0: per-host count of web interfaces (httpx / eyewitness /
    # nikto rows).  HostDetail.tsx uses this to gate the "Web
    # Interfaces" card visibility — fetch the full list lazily
    # only when the count is > 0.
    serialized["web_interface_count"] = (
        db.query(func.count(models.WebInterface.id))
        .filter(models.WebInterface.host_id == host_id)
        .scalar()
    ) or 0
    # v2.45.7 — gate the HostInspector NetExec card the same way as
    # the Web Interfaces card: a cheap count, full rows fetched lazily.
    serialized["netexec_result_count"] = (
        db.query(func.count(NetexecResult.id))
        .filter(NetexecResult.host_id == host_id)
        .scalar()
    ) or 0
    return serialized


@router.get(
    "/{host_id:int}/conflicts",
    response_model=HostConflictsResponse,
    responses={404: {"description": "Host not found"}},
    summary="Get host data conflicts",
)
def get_host_conflicts(host_id: int, db: Session = Depends(get_db), project: Project = Depends(get_current_project)):
    """Get confidence and conflict information for a host"""

    # Check if host exists and belongs to project
    host = db.query(models.Host).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Get host confidence data
    host_confidence = db.query(HostConfidence).filter(
        HostConfidence.host_id == host_id
    ).all()

    # Get port confidence data for this host
    port_confidence = db.query(PortConfidence).join(
        models.Port, PortConfidence.port_id == models.Port.id
    ).filter(
        models.Port.host_id == host_id
    ).all()

    # Get conflict history for this host
    host_conflicts = db.query(ConflictHistory).filter(
        ConflictHistory.object_type == 'host',
        ConflictHistory.object_id == host_id
    ).order_by(ConflictHistory.resolved_at.desc()).limit(10).all()

    # Get conflict history for ports of this host
    port_ids = db.query(models.Port.id).filter(models.Port.host_id == host_id).scalar_subquery()
    port_conflicts = db.query(ConflictHistory).filter(
        ConflictHistory.object_type == 'port',
        ConflictHistory.object_id.in_(port_ids)
    ).order_by(ConflictHistory.resolved_at.desc()).limit(10).all()

    # Format response
    confidence_data = []

    # Add host field confidence
    for conf in host_confidence:
        confidence_data.append({
            'id': conf.id,
            'field_name': conf.field_name,
            'confidence_score': conf.confidence_score,
            'scan_type': conf.scan_type,
            'data_source': conf.data_source,
            'method': conf.method,
            'scan_id': conf.scan_id,
            'updated_at': conf.updated_at.isoformat() if conf.updated_at else None,
            'additional_factors': conf.additional_factors,
            'object_type': 'host'
        })

    # Add port field confidence
    for conf in port_confidence:
        confidence_data.append({
            'id': conf.id,
            'field_name': f"port_{conf.port_id}_{conf.field_name}",
            'confidence_score': conf.confidence_score,
            'scan_type': conf.scan_type,
            'data_source': conf.data_source,
            'method': conf.method,
            'scan_id': conf.scan_id,
            'updated_at': conf.updated_at.isoformat() if conf.updated_at else None,
            'additional_factors': conf.additional_factors,
            'object_type': 'port',
            'port_id': conf.port_id
        })

    # Format conflict history
    conflicts = []
    for conflict in host_conflicts + port_conflicts:
        conflicts.append({
            'id': conflict.id,
            'object_type': conflict.object_type,
            'object_id': conflict.object_id,
            'field_name': conflict.field_name,
            'previous_value': conflict.previous_value,
            'previous_confidence': conflict.previous_confidence,
            'previous_scan_id': conflict.previous_scan_id,
            'previous_method': conflict.previous_method,
            'new_value': conflict.new_value,
            'new_confidence': conflict.new_confidence,
            'new_scan_id': conflict.new_scan_id,
            'new_method': conflict.new_method,
            'resolved_at': conflict.resolved_at.isoformat() if conflict.resolved_at else None
        })

    return {
        "confidence": confidence_data,
        "conflict_history": conflicts,
    }


# ---------------------------------------------------------------------------
# v2.90.0 — per-host DNS records (#44.1 follow-through, UX phase 3).
# ---------------------------------------------------------------------------

@router.get(
    "/{host_id:int}/dns-records",
    response_model=HostDnsRecordsResponse,
    responses={404: {"description": "Host not found"}},
    summary="DNS records associated with this host (v2.90.0)",
)
def get_host_dns_records(
    host_id: int,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Return the DNS records BlueStick has stored that pertain to this
    host.  Match rule (simple, covers the v1 cases):

      * ``value == host.ip_address``  — A / AAAA / PTR-of-this-IP
      * ``domain == host.hostname``   — records whose subject is the
        host's canonical name (CNAME / MX / NS / TXT / SOA / forward A
        of the host's own name)

    Pre-v2.89.0 every row's ``resolver_name`` is NULL (historical CSV
    DNSParser + amass uploads didn't carry the field).  Fresh dnsx
    ingests populate it per-record so the card can surface "which
    resolver answered what".  Aliased hostnames discovered via PTR
    against ``host.ip_address`` are NOT auto-followed in v1 — that
    would require another query layer and is rarer than the common
    case; the card can suggest the operator search the canonical
    hostname directly if needed.
    """
    host = (
        db.query(models.Host)
        .filter(
            models.Host.id == host_id,
            models.Host.project_id == project.id,
        )
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    filters = [models.DNSRecord.value == host.ip_address]
    if host.hostname:
        filters.append(models.DNSRecord.domain == host.hostname)

    rows = (
        db.query(models.DNSRecord)
        .filter(
            models.DNSRecord.project_id == project.id,
            or_(*filters),
        )
        .order_by(
            models.DNSRecord.record_type.asc(),
            models.DNSRecord.domain.asc(),
            models.DNSRecord.value.asc(),
            models.DNSRecord.id.asc(),
        )
        .limit(limit)
        .all()
    )

    items = [
        HostDnsRecordRow(
            id=r.id,
            domain=r.domain,
            record_type=r.record_type,
            value=r.value,
            ttl=r.ttl,
            resolver_name=r.resolver_name,
            created_at=r.created_at,
        )
        for r in rows
    ]
    # Distinct resolver + record-type lists, preserved in stable
    # alphabetical order so the frontend can render summary pills
    # without a second client-side pass.
    resolvers = sorted({r.resolver_name for r in rows if r.resolver_name})
    record_types = sorted({r.record_type for r in rows if r.record_type})
    return HostDnsRecordsResponse(
        items=items,
        total=len(items),
        resolvers=resolvers,
        record_types=record_types,
    )


@router.get(
    "/tool-ready/{format}",
    responses={
        200: {
            "description": "Host list formatted for the target tool. "
            "Most formats return `text/plain`; `json` returns `application/json`. "
            "All include a `Content-Disposition: attachment` header.",
            "content": {
                "text/plain": {
                    "example": "10.0.0.1\n10.0.0.2\n10.0.0.3\n",
                },
                "application/json": {
                    "example": [{"ip_address": "10.0.0.1", "hostname": "web01", "ports": []}],
                },
            },
        },
        401: {"description": "Not authenticated"},
    },
    summary="Tool-ready host export",
)
def get_tool_ready_hosts(
    format: str,
    filters: HostFilterParams = Depends(),
    # Tool-ready-only params (not part of the shared filter bundle).
    scan_id: Optional[int] = Query(None, description="Filter by specific scan ID"),
    include_ports: Optional[bool] = Query(False, description="Include port information in output"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate tool-ready output for filtered hosts.

    Supported formats:
    - **ip-list** — one IP per line
    - **nmap** — Nmap-compatible target list
    - **metasploit** — Metasploit RHOSTS format
    - **masscan** — Masscan target format
    - **nuclei** — Nuclei target format
    - **host-port** — IP:PORT for each open port
    - **json** — JSON array with host details

    v2.93.0 — converged onto the shared ``HostFilterParams`` bundle, so
    this export now honours every Hosts-page filter (tags, labels,
    web/tech, the ``has:*`` family, and the ``q`` boolean query) instead
    of the partial subset it accepted before.
    """

    # Validate format
    supported_formats = ['ip-list', 'nmap', 'metasploit', 'masscan', 'nuclei', 'host-port', 'json']
    if format not in supported_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{format}'. Supported formats: {', '.join(supported_formats)}"
        )

    # Base query (reuse the filtering logic from get_hosts_v2)
    query = _build_filtered_host_query(
        db,
        current_user,
        **filters.as_builder_kwargs(),
        project_id=project.id,
    )

    # v2.90.1 — eager-load ports + scripts only for formats that
    # actually consume them.  Field-reported: a 42k-host project
    # crashed the backend with 502 on an ip-list export because the
    # unconditional selectinload pulled every port + every NSE script
    # for every host into RAM, OOM-killing the worker.  IP-only
    # formats walk just ``host.ip_address`` — no relationship access —
    # so skipping the eager-load shrinks the working set from
    # gigabytes to ~8 MB for the host rows themselves.  The
    # port-bearing formats (host-port, json with include_ports,
    # nuclei) still need the join; for those we keep the existing
    # eager-load shape.  JSON without include_ports also skips.
    _NEEDS_PORT_DATA = {"host-port", "nuclei"}
    if format in _NEEDS_PORT_DATA or (format == "json" and include_ports):
        query = query.options(
            selectinload(models.Host.ports).selectinload(models.Port.scripts),
            selectinload(models.Host.host_scripts),
        )

    if scan_id:
        host_ids_in_scan = db.query(models.HostScanHistory.host_id).filter(
            models.HostScanHistory.scan_id == scan_id
        ).scalar_subquery()
        query = query.filter(models.Host.id.in_(host_ids_in_scan))

    # Cap the result set so a project with hundreds of thousands of
    # deduplicated hosts can't OOM a worker materializing the full set
    # with eager-loaded ports + scripts.  The reports endpoint caps at
    # 10k; tool-ready is meant to be piped into scanners that happily
    # consume bigger lists, so we go higher — but still bounded.
    #
    # Truncation is signalled via response headers so callers (and the
    # frontend download dialog) can show a warning rather than silently
    # truncating.  The output body must stay clean for piping; injecting
    # warnings into the body would break consumers like
    # ``nmap -iL tool-ready.txt``.
    MAX_TOOL_READY_HOSTS = 50_000
    total_count = query.with_entities(func.count(models.Host.id)).scalar() or 0
    truncated = total_count > MAX_TOOL_READY_HOSTS

    # IP-only formats reference nothing but ``host.ip_address``.  Loading
    # the full Host entity (every column) for a 42k-host project just to
    # read one string materialised hundreds of MB of ORM objects and
    # 502'd the worker — v2.90.1 dropped the port/script eager-load but
    # left the entity load in place.  Select the single column instead
    # (the same pattern the matching-ids endpoint at get_hosts_matching_ids
    # already uses) and join directly, keeping the working set at ~MB.
    _IP_ONLY_JOINERS = {"ip-list": "\n", "nmap": " ", "metasploit": " ", "masscan": ","}
    if format in _IP_ONLY_JOINERS:
        ip_rows = (
            query.with_entities(models.Host.ip_address)
            .limit(MAX_TOOL_READY_HOSTS)
            .all()
        )
        hosts_returned = len(ip_rows)
        output = _IP_ONLY_JOINERS[format].join(row[0] for row in ip_rows if row[0])
    else:
        hosts = query.limit(MAX_TOOL_READY_HOSTS).all()
        hosts_returned = len(hosts)
        # The per-host port narrowing in _generate_tool_output reuses the
        # same port-dimension filters the query applied, sourced from the
        # shared bundle.
        output_filters = {
            "search": filters.search,
            "ports": filters.ports,
            "services": filters.services,
            "port_states": filters.port_states,
            "has_open_ports": filters.has_open_ports,
        }
        output = _generate_tool_output(hosts, format, include_ports, output_filters)

    # Set appropriate content type and filename
    content_type, filename = _get_content_type_and_filename(format)

    response_headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "X-Tool-Ready-Total": str(total_count),
        "X-Tool-Ready-Returned": str(hosts_returned),
    }
    if truncated:
        response_headers["X-Tool-Ready-Truncated"] = "true"
        response_headers["X-Tool-Ready-Limit"] = str(MAX_TOOL_READY_HOSTS)

    return Response(
        content=output,
        media_type=content_type,
        headers=response_headers,
    )


def _get_filtered_output_ports(
    host: models.Host,
    filters: Optional[Dict[str, Optional[str | bool]]] = None,
) -> List[models.Port]:
    filters = filters or {}
    ports = list(host.ports or [])

    port_values = {
        int(value.strip())
        for value in (filters.get("ports") or "").split(",")
        if value and value.strip().isdigit()
    }
    if port_values:
        ports = [port for port in ports if port.port_number in port_values]

    service_values = [
        value.strip().lower()
        for value in (filters.get("services") or "").split(",")
        if value and value.strip()
    ]
    if service_values:
        service_ports = {
            mapped_port
            for service in service_values
            for mapped_port in SERVICE_PORT_MAPPINGS.get(service, [])
        }
        ports = [
            port for port in ports
            if (
                (port.service_name and any(service in port.service_name.lower() for service in service_values))
                or port.port_number in service_ports
            )
        ]

    port_state_values = {
        value.strip().lower()
        for value in (filters.get("port_states") or "").split(",")
        if value and value.strip()
    }
    if port_state_values:
        ports = [port for port in ports if (port.state or "").lower() in port_state_values]

    if filters.get("has_open_ports"):
        ports = [port for port in ports if port.state == 'open']

    search_value = (filters.get("search") or "").strip().lower()
    if search_value:
        if search_value.isdigit():
            search_port = int(search_value)
            ports = [
                port for port in ports
                if port.port_number == search_port
                or (port.service_name and search_value in port.service_name.lower())
                or (port.service_product and search_value in port.service_product.lower())
            ]
        else:
            mapped_ports = set(SERVICE_PORT_MAPPINGS.get(search_value, []))
            ports = [
                port for port in ports
                if (
                    (port.service_name and search_value in port.service_name.lower())
                    or (port.service_product and search_value in port.service_product.lower())
                    or port.port_number in mapped_ports
                )
            ]

    return ports


def _generate_tool_output(
    hosts: List[models.Host],
    format: str,
    include_ports: bool = False,
    filters: Optional[Dict[str, Optional[str | bool]]] = None,
) -> str:
    """Generate tool-specific output format"""
    
    if format == 'ip-list':
        # Simple list of IP addresses
        return '\n'.join([host.ip_address for host in hosts])
    
    elif format == 'nmap':
        # Nmap-compatible target list (space-separated)
        return ' '.join([host.ip_address for host in hosts])
    
    elif format == 'metasploit':
        # Metasploit RHOSTS format (space-separated)
        return ' '.join([host.ip_address for host in hosts])
    
    elif format == 'masscan':
        # Masscan target format (comma-separated)
        return ','.join([host.ip_address for host in hosts])
    
    elif format == 'nuclei':
        # Nuclei target format - URLs for web services, IPs for others
        targets = []
        for host in hosts:
            # Check if host has web ports
            web_ports = []
            filtered_ports = _get_filtered_output_ports(host, filters)
            for port in filtered_ports:
                if port.state == 'open' and port.port_number in [80, 443, 8000, 8080, 8081, 8008, 8443, 8444, 8888]:
                    web_ports.append(port.port_number)
            
            if web_ports:
                # Generate URLs for web ports
                for port_num in web_ports:
                    protocol = 'https' if port_num in [443, 8443, 8444] else 'http'
                    if port_num in [80, 443]:
                        targets.append(f"{protocol}://{host.ip_address}")
                    else:
                        targets.append(f"{protocol}://{host.ip_address}:{port_num}")
            else:
                # Just add IP for non-web hosts
                targets.append(host.ip_address)
        
        return '\n'.join(targets)
    
    elif format == 'host-port':
        # IP:PORT format for each open port
        results = []
        for host in hosts:
            open_ports = [port for port in _get_filtered_output_ports(host, filters) if port.state == 'open']
            if open_ports:
                for port in open_ports:
                    results.append(f"{host.ip_address}:{port.port_number}")
            else:
                # Include hosts without open ports as just IP
                results.append(host.ip_address)
        
        return '\n'.join(results)
    
    elif format == 'json':
        # JSON format with host details
        host_data = []
        for host in hosts:
            host_info = {
                'ip_address': host.ip_address,
                'hostname': host.hostname,
                'state': host.state,
                'os_name': host.os_name,
                'os_family': host.os_family
            }
            
            if include_ports:
                filtered_ports = _get_filtered_output_ports(host, filters)
                host_info['ports'] = [
                    {
                        'port': port.port_number,
                        'protocol': port.protocol,
                        'state': port.state,
                        'service': port.service_name,
                        'product': port.service_product,
                        'version': port.service_version
                    }
                    for port in filtered_ports
                ]
            
            host_data.append(host_info)
        
        return json.dumps(host_data, indent=2)
    
    else:
        return '\n'.join([host.ip_address for host in hosts])


def _get_content_type_and_filename(format: str) -> tuple:
    """Get content type and filename for different formats"""

    format_config = {
        'ip-list': ('text/plain', 'hosts.txt'),
        'nmap': ('text/plain', 'nmap-targets.txt'),
        'metasploit': ('text/plain', 'msf-targets.txt'),
        'masscan': ('text/plain', 'masscan-targets.txt'),
        'nuclei': ('text/plain', 'nuclei-targets.txt'),
        'host-port': ('text/plain', 'host-ports.txt'),
        'json': ('application/json', 'hosts.json')
    }

    return format_config.get(format, ('text/plain', 'hosts.txt'))


# Saved Hosts-page filter views (/hosts/views CRUD) were carved out to
# app/api/v1/endpoints/host_filter_views.py in v2.71.0 under the
# file-size policy.  Paths are unchanged — that router mounts at the
# same /hosts prefix.


# ---------------------------------------------------------------------------
# Web interfaces (v2.12.0) — unified per-host view of httpx / eyewitness /
# nikto / etc. fingerprint output.  See db/models.WebInterface for the
# storage shape.
# ---------------------------------------------------------------------------

class WebInterfaceResponse(BaseModel):
    id: int
    source: str
    url: str
    protocol: Optional[str] = None
    port: Optional[int] = None
    status_code: Optional[int] = None
    title: Optional[str] = None
    server_header: Optional[str] = None
    content_length: Optional[int] = None
    technologies: Optional[List[str]] = None
    favicon_hash: Optional[str] = None
    tls_info: Optional[dict] = None
    has_screenshot: bool = False
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    scan_id: int
    port_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


@router.get(
    "/{host_id:int}/web-interfaces",
    response_model=List[WebInterfaceResponse],
    summary="Web interfaces (httpx / eyewitness / nikto) observed on a host",
)
def list_host_web_interfaces(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """List all web interfaces discovered on this host across any scan
    or recon session.  Deduped by ``(url, source)`` via the underlying
    unique constraint — each row is one tool's observation of one URL.

    Aggregates eyewitness, httpx, nikto, and any future
    web-fingerprint tools that write to the ``web_interfaces`` table.
    """
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == project.id)
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rows = (
        db.query(models.WebInterface)
        .filter(models.WebInterface.host_id == host_id)
        .order_by(models.WebInterface.port.asc().nulls_last(), models.WebInterface.url)
        .all()
    )
    return [
        WebInterfaceResponse(
            id=r.id,
            source=r.source,
            url=r.url,
            protocol=r.protocol,
            port=r.port,
            status_code=r.status_code,
            title=r.title,
            server_header=r.server_header,
            content_length=r.content_length,
            technologies=r.technologies or [],
            favicon_hash=r.favicon_hash,
            tls_info=r.tls_info,
            has_screenshot=bool(r.screenshot_path),
            first_seen=r.first_seen,
            last_seen=r.last_seen,
            scan_id=r.scan_id,
            port_id=r.port_id,
        )
        for r in rows
    ]


class NetexecResultResponse(BaseModel):
    """One NetExec (credentialed-enumeration) observation of a host.

    v2.45.7 — the `netexec_results` table was populated by the
    netexec parser but had no API surface, so SMB share enumeration
    and credentialed-access confirmation were invisible to operators.
    """
    id: int
    scan_id: int
    protocol: str
    port: Optional[int] = None
    auth_success: Optional[bool] = None
    username: Optional[str] = None
    domain: Optional[str] = None
    hostname: Optional[str] = None
    domain_name: Optional[str] = None
    os_version: Optional[str] = None
    # `shares` is parser-shaped JSON (the netexec output varies); the
    # frontend renders it defensively.
    shares: Optional[Any] = None
    first_seen: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


@router.get(
    "/{host_id:int}/netexec",
    response_model=List[NetexecResultResponse],
    summary="NetExec credentialed-enumeration results observed on a host",
)
def list_host_netexec_results(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """List NetExec results for this host — one row per protocol probe
    (smb / ldap / winrm / rdp).  Carries the authentication outcome and
    any enumerated SMB shares.

    Lazy-loaded by the HostInspector NetExec card, gated on the
    ``netexec_result_count`` returned by the host-detail endpoint.
    """
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == project.id)
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rows = (
        db.query(NetexecResult)
        .filter(NetexecResult.host_id == host_id)
        .order_by(NetexecResult.protocol, NetexecResult.id)
        .all()
    )
    return [
        NetexecResultResponse(
            id=r.id,
            scan_id=r.scan_id,
            protocol=r.protocol,
            port=r.port,
            auth_success=r.auth_success,
            username=r.username,
            domain=r.domain,
            hostname=r.hostname,
            domain_name=r.domain_name,
            os_version=r.os_version,
            shares=r.shares,
            # NetexecResult model carries `discovered_at` (server_default
            # = func.now() when the row is inserted), not `first_seen`.
            # The response schema field is named `first_seen` for parity
            # with the WebInterface response above — both surface "when
            # did we first observe this artefact" to the analyst.  Pre-
            # fix this attribute access raised AttributeError and 500'd
            # the netexec card.
            first_seen=r.discovered_at,
        )
        for r in rows
    ]


@router.get(
    "/web-interfaces/{interface_id:int}/screenshot",
    summary="Stream a screenshot PNG captured by EyeWitness",
)
def get_web_interface_screenshot(
    interface_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Stream the PNG screenshot for a web_interfaces row, if one was
    extracted from an EyeWitness zip bundle at ingest time.

    The stored ``screenshot_path`` is a relative path under
    ``uploads/web_screenshots/{scan_id}/``; the endpoint resolves it
    safely (rejecting any path traversal) and streams the bytes.
    Returns 404 when the row has no screenshot or the file is
    missing on disk (e.g. CSV-only EyeWitness uploads).
    """
    from fastapi.responses import FileResponse
    from app.core.config import settings
    import os

    row = (
        db.query(models.WebInterface)
        .filter(
            models.WebInterface.id == interface_id,
            models.WebInterface.project_id == project.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Web interface not found")
    if not row.screenshot_path:
        raise HTTPException(status_code=404, detail="No screenshot captured for this interface")

    # Defense in depth against path traversal — the parser already
    # stores basenames, but normalize and verify the resolved path
    # stays inside the web_screenshots root before serving.
    base = Path(settings.UPLOAD_DIR) / "web_screenshots"
    try:
        target = (base / row.screenshot_path).resolve()
        base_resolved = base.resolve()
        target.relative_to(base_resolved)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Screenshot path invalid")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Screenshot file missing on disk")

    return FileResponse(
        path=str(target),
        media_type="image/png",
        filename=os.path.basename(str(target)),
    )


# ---------------------------------------------------------------------------
# Host workflow lineage (v3 alpha.9)
# ---------------------------------------------------------------------------
#
# Answers the host-centric question the v3 design review prioritised:
# "for this host, what's been done to it?"  Returns the recon sessions
# that discovered it, the plans that include it, and the execution
# sessions that have produced results against it — in one call so the
# HostDetail panel renders without N+1 queries against three
# different surfaces.

class HostLineageReconRow(BaseModel):
    session_id: int
    scope_id: int
    scope_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    started_by_username: Optional[str] = None


class HostLineagePlanRow(BaseModel):
    plan_id: int
    title: str
    status: str
    version: int
    entry_id: int
    entry_status: str
    created_at: datetime
    generated_by_model: Optional[str] = None
    source_kind: Optional[str] = None


class HostLineageExecutionRow(BaseModel):
    execution_session_id: int
    plan_id: int
    plan_title: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    generated_by_model: Optional[str] = None
    started_by_username: Optional[str] = None
    # Per-host counts within this session — quick read for "did this
    # session test this host, and was anything found?"
    test_count: int = 0
    finding_count: int = 0


class HostLineageResponse(BaseModel):
    host_id: int
    ip_address: str
    recon_sessions: List[HostLineageReconRow] = Field(default_factory=list)
    plan_entries: List[HostLineagePlanRow] = Field(default_factory=list)
    execution_sessions: List[HostLineageExecutionRow] = Field(default_factory=list)


@router.get(
    "/{host_id:int}/lineage",
    response_model=HostLineageResponse,
    summary="Workflow lineage for one host (v3 alpha.9)",
)
def get_host_lineage(
    host_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Return every workflow run that has touched this host.

    Three sections — recon sessions that discovered it (via
    HostScanHistory → IngestionJob → ReconSession), plan entries
    referencing it (via TestPlanEntry → TestPlan), and execution
    sessions that have produced per-test results against any of those
    entries (via TestExecutionResult → ExecutionSession).  Each row
    is the minimum needed for the UI's "Workflow lineage" panel —
    deeper detail lives on the per-session pages, linked from each
    row.
    """
    from app.db.models_agent import (
        ExecutionSession,
        ReconSession,
        TestExecutionResult,
    )

    host = (
        db.query(models.Host)
        .filter(
            models.Host.id == host_id,
            models.Host.project_id == project.id,
        )
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # --- Recon sessions that discovered this host ---------------------
    # Path: HostScanHistory.host_id → Scan → IngestionJob.scan_id +
    # IngestionJob.recon_session_id → ReconSession.
    #
    # Resolved in two queries: first collect distinct session IDs
    # (selecting whole ReconSession rows with DISTINCT fails on
    # Postgres because the ``environment`` JSON column has no
    # equality operator), then load the full session objects in a
    # second query keyed on those IDs.
    recon_ids = [
        row[0] for row in (
            db.query(ReconSession.id)
            .join(
                models.IngestionJob,
                models.IngestionJob.recon_session_id == ReconSession.id,
            )
            .join(
                models.HostScanHistory,
                models.HostScanHistory.scan_id == models.IngestionJob.scan_id,
            )
            .filter(models.HostScanHistory.host_id == host_id)
            .distinct()
            .all()
        )
    ]
    recon_rows: list = []
    if recon_ids:
        recon_rows = (
            db.query(ReconSession)
            .filter(ReconSession.id.in_(recon_ids))
            .order_by(ReconSession.started_at.desc())
            .all()
        )
    scope_ids = {r.scope_id for r in recon_rows if r.scope_id is not None}
    scope_name_by_id = {}
    if scope_ids:
        scope_name_by_id = dict(
            db.query(models.Scope.id, models.Scope.name)
            .filter(models.Scope.id.in_(scope_ids))
            .all()
        )
    recon_out = [
        HostLineageReconRow(
            session_id=r.id,
            scope_id=r.scope_id,
            scope_name=scope_name_by_id.get(r.scope_id),
            status=r.status,
            started_at=r.started_at,
            completed_at=r.completed_at,
            generated_by_model=r.generated_by_model,
            generated_by_tool=r.generated_by_tool,
            started_by_username=(
                r.started_by.username if r.started_by else None
            ),
        )
        for r in recon_rows
    ]

    # --- Plan entries referencing this host ---------------------------
    # Project-scope through the plan's project_id (the entry FK doesn't
    # carry project_id directly).  One row per (plan, entry) since a
    # host can appear at most once per plan (uq_plan_host).
    plan_entries = (
        db.query(TestPlanEntry, TestPlan)
        .join(TestPlan, TestPlan.id == TestPlanEntry.test_plan_id)
        .filter(
            TestPlanEntry.host_id == host_id,
            TestPlan.project_id == project.id,
        )
        .order_by(TestPlan.created_at.desc())
        .all()
    )
    plan_out = [
        HostLineagePlanRow(
            plan_id=plan.id,
            title=plan.title,
            status=plan.status,
            version=plan.version,
            entry_id=entry.id,
            entry_status=entry.status,
            created_at=plan.created_at,
            generated_by_model=plan.generated_by_model,
            source_kind=plan.source_kind,
        )
        for entry, plan in plan_entries
    ]

    # --- Execution sessions that have touched this host ---------------
    # Path: TestPlanEntry.host_id → TestExecutionResult.entry_id →
    # ExecutionSession.id.  Distinct sessions, with per-session test +
    # finding counts for this host computed in one batch.
    entry_ids = [e.id for e, _ in plan_entries]
    execution_out: List[HostLineageExecutionRow] = []
    if entry_ids:
        # Distinct sessions that have at least one result against any
        # of this host's entries.  Same JSON-DISTINCT issue as the
        # recon query above — collect IDs first, then load full rows.
        exec_ids = [
            row[0] for row in (
                db.query(ExecutionSession.id)
                .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
                .join(
                    TestExecutionResult,
                    TestExecutionResult.execution_session_id == ExecutionSession.id,
                )
                .filter(
                    TestExecutionResult.entry_id.in_(entry_ids),
                    TestPlan.project_id == project.id,
                )
                .distinct()
                .all()
            )
        ]
        session_rows = []
        if exec_ids:
            session_rows = (
                db.query(ExecutionSession, TestPlan)
                .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
                .filter(ExecutionSession.id.in_(exec_ids))
                .order_by(ExecutionSession.started_at.desc())
                .all()
            )
        # Counts: tests run against this host's entries + how many of
        # those tests are findings.  Two cheap counts per session at
        # the current scale (typically O(1) sessions per host); using
        # two filtered count() queries instead of SUM(CAST(...)) keeps
        # the SQL portable across Postgres and SQLite tests.
        for sess, plan in session_rows:
            test_count = (
                db.query(func.count(TestExecutionResult.id))
                .filter(
                    TestExecutionResult.execution_session_id == sess.id,
                    TestExecutionResult.entry_id.in_(entry_ids),
                )
                .scalar()
            ) or 0
            finding_count = (
                db.query(func.count(TestExecutionResult.id))
                .filter(
                    TestExecutionResult.execution_session_id == sess.id,
                    TestExecutionResult.entry_id.in_(entry_ids),
                    TestExecutionResult.is_finding.is_(True),
                )
                .scalar()
            ) or 0
            execution_out.append(
                HostLineageExecutionRow(
                    execution_session_id=sess.id,
                    plan_id=plan.id,
                    plan_title=plan.title,
                    status=sess.status,
                    started_at=sess.started_at,
                    completed_at=sess.completed_at,
                    generated_by_model=sess.generated_by_model,
                    started_by_username=(
                        sess.started_by.username if sess.started_by else None
                    ),
                    test_count=int(test_count or 0),
                    finding_count=int(finding_count or 0),
                )
            )

    return HostLineageResponse(
        host_id=host.id,
        ip_address=host.ip_address,
        recon_sessions=recon_out,
        plan_entries=plan_out,
        execution_sessions=execution_out,
    )
