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
import itertools
import time

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload, noload, aliased
from sqlalchemy import or_, and_, case, distinct, func, true

from app.core.config import settings
from app.db.session import disable_statement_timeout, get_db, is_statement_timeout
from app.api.deps import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.api.params import HostFilterParams
from app.db.models_project import Project, ProjectRole
from app.db.models_auth import User
from app.db import models
from app.db.models_confidence import (
    NETEXEC_RAW_OUTPUT_LIMIT, HostConfidence, PortConfidence, NetexecResult,
)
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_host_tests import HostTest, ACTIVE_TEST_STATUSES, TESTED_OUTCOMES
from app.db.models_proposals import EvidenceRecord
from app.services.host_serialization import _serialize_follow, _serialize_note, note_load_options  # CR4-2
from app.services.note_attachment_service import require_readable_file
from app.services import host_query
from app.services import host_query_predicates as P
from app.services.scan_time import scan_time_for_api
from app.schemas.schemas import (
    Host as HostSchema,
    ScanHost as ScanHostSchema,
    HostListResponse,
)
from pydantic import BaseModel, ConfigDict
from app.services.vulnerability_service import VulnerabilityService
from app.services.host_follow_service import HostFollowService
from app.services.host_query import (
    SERVICE_PORT_MAPPINGS,
    escape_like as _escape_like,
    build_filtered_host_query as _build_filtered_host_query,
    apply_host_sorting as _apply_host_sorting,
    HOST_SORT_FIELDS,
    WEAKNESS_FLAGS,
    WEAKNESS_LABELS,
    host_weakness_flags,
    weakness_descriptions,
    weakness_predicate,
)
from app.services.host_serialization import (
    LIST_DISCOVERY_CAP,
    discovery_dict as _discovery_dict,
    exploit_count_maps as _exploit_count_maps,
    serialize_host_base as _serialize_host_base,
    serialize_host_detail as _serialize_host_detail,
    serialize_port_light as _serialize_port_light,
)
from app.db.models import HostFollow, FollowStatus


# --- Response schemas for previously untyped endpoints ---

class PortFilterItem(BaseModel):
    port: int
    service: str = "unknown"
    state: Optional[str] = None
    count: int = 0
    # Distinct hosts per state combination the endpoint editor can choose
    # ("open", "closed,open"…, "any") — the picker's count before applying.
    state_counts: Dict[str, int] = {}

class ServiceFilterItem(BaseModel):
    name: str
    count: int = 0
    state_counts: Dict[str, int] = {}

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


class SiteFilterItem(BaseModel):
    """A site name (from Subnet.site) with its distinct in-scope host count,
    for the Site filter combobox."""
    name: str
    host_count: int = 0


# RDAP network-attribution facets. Empty unless the project has ingested RDAP
# output — the frontend hides the corresponding controls when a list is empty,
# so projects without attribution don't see filters that can't match anything.
class AttributionOrgFilterItem(BaseModel):
    name: str
    host_count: int = 0


class AttributionAsnFilterItem(BaseModel):
    asn: int
    as_name: Optional[str] = None
    host_count: int = 0


class AttributionCountryFilterItem(BaseModel):
    country: str
    host_count: int = 0


class WeaknessFilterItem(BaseModel):
    """A weakness / access flag (the DSL's has: value) for the catalog (v2.423.0)."""
    name: str
    label: str
    description: str
    host_count: int = 0


class CheckFilterItem(BaseModel):
    """A misconfiguration check some host in the project carries (v2.423.0)."""
    id: str
    title: str
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
    # The endpoint built a `sites` list all along, but it was missing from this
    # response_model so FastAPI silently stripped it — the Site filter dropdown
    # rendered empty even with sites configured. Declared here so it ships.
    sites: List[SiteFilterItem] = []
    # RDAP network attribution — distinct owners / ASNs / countries observed on
    # in-scope hosts, for the RDAP filter comboboxes. Empty when no RDAP data.
    orgs: List[AttributionOrgFilterItem] = []
    asns: List[AttributionAsnFilterItem] = []
    countries: List[AttributionCountryFilterItem] = []
    # v2.423.0 — weakness / access flags (every flag, counted) and the
    # misconfiguration checks present in the project.
    weaknesses: List[WeaknessFilterItem] = []
    checks: List[CheckFilterItem] = []

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
    # v2.367.0 — declared HERE or the response model strips them (it did: the
    # handler set them and the panel still read "scan #82" with no "shown").
    previous_scan_filename: Optional[str] = None
    new_scan_filename: Optional[str] = None
    current_value: Optional[str] = None

class HostConflictsResponse(BaseModel):
    # Canonical host-level conflict count (same definition as the Hosts-list
    # badge — see _host_conflict_counts).  The detail pane shows this, NOT the
    # length of `confidence` (which is per-field confidence records, host AND
    # port, and is not a conflict count).
    conflict_count: int = 0
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
    # Total DNS records ingested for the whole project — lets the host card
    # distinguish "no DNS data at all" from "N records ingested, none match
    # this host" instead of silently rendering nothing.
    project_total: int = 0


router = APIRouter(dependencies=[Depends(get_current_user)])


# ``HostFilterParams`` — the shared /hosts filter query params — is declared in
# ``app/api/params.py`` (review 2026-10-01 B4: the reports router imported it
# from this file).  Imported above, so ``hosts.HostFilterParams`` still names
# the one class.


_ISSUE_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
_RANK_NAME = {4: "critical", 3: "high", 2: "medium", 1: "low"}


def _issue_counts(db: Session, host_ids: List[int]) -> Dict[int, Dict[str, int]]:
    """v2.415.0 — each host's ISSUES by worst severity (the key the
    inspector and Findings group by), and how many are misconfigurations.
    Informational rows are not attention.  One grouped query."""
    if not host_ids:
        return {}
    from sqlalchemy import case

    rank = func.max(case(*[(Vulnerability.severity == name, r) for name, r in _ISSUE_RANK.items()], else_=0))
    key = func.coalesce(Vulnerability.issue_key, func.concat("row:", Vulnerability.id))
    out: Dict[int, Dict[str, int]] = {}
    rows = (
        db.query(Vulnerability.host_id, key, rank,
                 func.max(case((Vulnerability.check_id.isnot(None), 1), else_=0)))
        .filter(Vulnerability.host_id.in_(host_ids))
        .group_by(Vulnerability.host_id, key)
        .all()
    )
    for host_id, _key, worst, misconfig in rows:
        name = _RANK_NAME.get(int(worst or 0))
        if not name:
            continue
        counts = out.setdefault(host_id, {"critical": 0, "high": 0, "medium": 0, "low": 0, "misconfiguration": 0})
        counts[name] += 1
        if misconfig:
            counts["misconfiguration"] += 1
    return out


# v2.428.0 — the host-detail facts moved to app/services/host_detail_service.py
# so the agent's host detail states the same numbers; same names kept here for
# the endpoints and their tests.
from app.services.host_detail_service import (  # noqa: E402
    active_finding_counts as _active_finding_counts,
    cert_web_interfaces as _cert_web_interfaces,
    host_assignees as _host_assignees,
    host_conflict_counts as _host_conflict_counts,
    host_conflict_history as _host_conflict_history,
)


# v2.347.0 — the "changed at its latest scan" derivation moved to
# app/services/host_change_service.py so the investigation queue on My Work
# shares it without a service importing a router.  Same name kept here for
# the list endpoint and its tests.
from app.services.host_change_service import (  # noqa: E402
    hosts_changed_since_prior_scan as _hosts_changed_since_prior_scan,
)


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
    sort_by: str = Query("critical_vulns", pattern=f"^({'|'.join(HOST_SORT_FIELDS)})$"),
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
        # The project's host count beside the matching one: a saved default
        # view narrowed the list to "157 matching hosts" with nothing saying
        # out of how many.  One index-only count on (project_id, ip_address).
        project_total = (
            db.query(func.count(models.Host.id))
            .filter(models.Host.project_id == project.id)
            .scalar()
        )
    else:
        total = None
        project_total = None
    query = _apply_host_sorting(query, sort_by, sort_order)

    # Loading for the listing response.  Host's relationships are plain lazy,
    # so only what the row reads is named: its ports and its tags (with each
    # tag's definition, read by serialize_host_base).  The list renders no NSE
    # script bodies — ports go out script-free (serialize_port_light) and
    # host_scripts is passed to the serializer as [] — and no vulnerability,
    # attribute, note or scan-history rows: counts, the 3 newest notes and the
    # 6 newest discoveries come from the grouped / window queries below.
    # ``tests/test_host_list_query_budget.py`` bounds the page's statements.
    query = query.options(
        selectinload(models.Host.ports),
        selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
        # Redundant with the lazy defaults (nothing below reads these); kept
        # so a reader of one by mistake gets nothing rather than a query per
        # host.
        noload(models.Host.vulnerabilities),
        noload(models.Host.attributes),
        noload(models.Host.notes),
        noload(models.Host.host_scripts),
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
                .options(*note_load_options())
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
    except Exception as exc:
        # A statement the API timeout cancelled is the request's answer (503,
        # from get_db) — and for anything else the failed statement has
        # aborted the transaction, so undo it or every query below fails with
        # "current transaction is aborted" and the page answers 500.
        if is_statement_timeout(exc):
            raise
        db.rollback()
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

    tp_count_map = dict(db.query(HostTest.host_id, func.count(HostTest.id)).filter(
        HostTest.host_id.in_(host_ids), HostTest.status.in_(ACTIVE_TEST_STATUSES),
    ).group_by(HostTest.host_id).all()) if host_ids else {}
    te_count_map = dict(db.query(EvidenceRecord.host_id, func.count(EvidenceRecord.id)).filter(
        EvidenceRecord.host_id.in_(host_ids), EvidenceRecord.outcome.in_(TESTED_OUTCOMES),
    ).group_by(EvidenceRecord.host_id).all()) if host_ids else {}

    # Batch lookup: NetExec result counts per host — surfaces the "NetExec /
    # credential checks ran" signal as a Hosts-list badge.  One grouped query
    # per page (mirrors te_count_map); netexec_results is indexed by host_id.
    netexec_count_map: Dict[int, int] = {}
    if host_ids:
        netexec_count_map = dict(
            db.query(NetexecResult.host_id, func.count(NetexecResult.id))
            .filter(NetexecResult.host_id.in_(host_ids))
            .group_by(NetexecResult.host_id)
            .all()
        )

    # Batch lookup: host-level data conflicts (scans disagreed on a field) —
    # surfaces the confidence/conflict subsystem's "this host's data is
    # contested" signal as a list badge.  Shares ONE definition with the
    # host-detail conflicts pane via _host_conflict_counts (see its docstring).
    conflict_count_map = _host_conflict_counts(db, host_ids)

    # Batch lookup: web interface counts per host (v2.12.0).
    # Drives the "Web" badge on the Hosts list (phase 2 UI) and
    # feeds the per-host HostDetail card count.
    # v2.362.0 — DISTINCT interfaces (tool + URL), not rows: web_interfaces
    # keeps one row per scan, so a host re-scanned once showed "4 web" over a
    # section listing 2. Same key as the inspector's latestObservations.
    wi_count_map: Dict[int, int] = {}
    if host_ids:
        distinct_wi = (
            db.query(
                models.WebInterface.host_id,
                models.WebInterface.source,
                models.WebInterface.url,
            )
            .filter(models.WebInterface.host_id.in_(host_ids))
            .distinct()
            .subquery()
        )
        wi_rows = (
            db.query(distinct_wi.c.host_id, func.count())
            .group_by(distinct_wi.c.host_id)
            .all()
        )
        wi_count_map = {row[0]: row[1] for row in wi_rows}

    # Batch lookup: team review state per host (v4.9.1, extended for team-
    # shared review).  Review is a team activity and the list filter classifies
    # hosts by team state, so the row must surface team state too — otherwise a
    # host the filter returns as "Reviewed" would show no reviewer.  One query
    # for the whole page — not N+1.
    #
    # We split TEAMMATES (excluding the caller — their own state is on the
    # interactive Follow control, and including them double-rendered the badge)
    # into in_review (``other_review_map``, kept under the existing
    # ``other_reviewers`` field) and reviewed (``reviewed_map``).  Separately we
    # derive ``team_status_map``: the most-advanced state across ALL users
    # INCLUDING the caller (reviewed outranks in_review), which is what the
    # filter matches on.
    other_review_map: Dict[int, list] = {}
    reviewed_map: Dict[int, list] = {}
    team_status_map: Dict[int, str] = {}
    if host_ids:
        review_rows = (
            db.query(
                HostFollow.host_id, HostFollow.user_id, HostFollow.status,
                User.username, User.full_name,
            )
            .join(User, HostFollow.user_id == User.id)
            .filter(
                HostFollow.host_id.in_(host_ids),
                HostFollow.status.in_([
                    FollowStatus.IN_REVIEW.value, FollowStatus.REVIEWED.value,
                ]),
            )
            .all()
        )
        for hid, uid, status, username, full_name in review_rows:
            # Team status across ALL users (caller included): reviewed wins.
            if status == FollowStatus.REVIEWED.value:
                team_status_map[hid] = FollowStatus.REVIEWED.value
            elif team_status_map.get(hid) != FollowStatus.REVIEWED.value:
                team_status_map[hid] = FollowStatus.IN_REVIEW.value
            # Teammate display lists exclude the caller (own state = control).
            if uid != current_user.id:
                entry = {"user_id": uid, "name": full_name or username}
                if status == FollowStatus.REVIEWED.value:
                    reviewed_map.setdefault(hid, []).append(entry)
                else:
                    other_review_map.setdefault(hid, []).append(entry)

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
    # each host.  "Active" = the finding is still being worked AND this host's
    # own endpoint is live: a host dismissed as a false positive (or recorded
    # remediated) on a finding kept counting it (review 2026-09-23 R7).  One
    # grouped query for the page — not N+1.
    finding_count_map: Dict[int, int] = _active_finding_counts(db, host_ids)

    # "Changed since last scan" — host ids whose most-recent scan flipped state
    # or added a port vs the prior scan. Batched window-function query.
    changed_map = _hosts_changed_since_prior_scan(db, host_ids)

    # Batch lookup: count of exploitable vulns per host — drives the Attention
    # column's "exploit available" reason.  One grouped query for the page.
    # v2.344.0 — also the CRITICAL-and-exploitable count, joined on the same
    # vulnerability row.  The badge used to pair the critical count with the
    # host-wide exploit count and then say "a critical with an exploit", a
    # claim nothing had actually checked.  v2.429.1 — shared with the agent's
    # host rows.
    exploit_count_map, critical_exploit_count_map = _exploit_count_maps(db, host_ids)

    # v2.415.0 — issues per worst severity (and misconfigurations) for the
    # Attention column: one grouped query for the page.
    issue_count_map = _issue_counts(db, host_ids)
    # v2.423.0 — the weakness / access flags per host: the row names the one
    # a weakness condition matched.  One statement for the page.
    weakness_flag_map = host_weakness_flags(db, current_user, project.id, host_ids)
    check_id_map: Dict[int, List[str]] = {}
    if host_ids:
        for hid, cid in (
            db.query(Vulnerability.host_id, Vulnerability.check_id)
            .filter(Vulnerability.host_id.in_(host_ids), Vulnerability.check_id.isnot(None))
            .distinct()
            .order_by(Vulnerability.host_id, Vulnerability.check_id)
            .all()
        ):
            check_id_map.setdefault(hid, []).append(cid)

    # Batch lookup: each host's MOST-SPECIFIC (longest-prefix) subnet + site,
    # so the Host column can show where the host lives.  Bounded by the page's
    # host_ids (paginated), not the whole project.  Most-specific-wins mirrors
    # the insights/attention resolution so overlapping ranges agree.
    host_location_map: Dict[int, dict] = {}
    if host_ids:
        best_prefix: Dict[int, int] = {}
        for hid, cidr, site in (
            db.query(models.HostSubnetMapping.host_id, models.Subnet.cidr, models.Subnet.site)
            .join(models.Subnet, models.HostSubnetMapping.subnet_id == models.Subnet.id)
            .filter(models.HostSubnetMapping.host_id.in_(host_ids))
            .all()
        ):
            try:
                pfx = ipaddress.ip_network(cidr, strict=False).prefixlen
            except ValueError:
                pfx = 0
            if hid not in best_prefix or pfx > best_prefix[hid]:
                best_prefix[hid] = pfx
                host_location_map[hid] = {"subnet": cidr, "site": site}

    # v2.344.0 — the same three coverage states the detail card shows
    # (subnet / reachable via in-scope name / none), so the list can't call a
    # host behind an approved name "out of scope".  One query for the page;
    # hosts with a subnet mapping above are already known.
    scope_coverage_map: Dict[int, str] = {}
    project_has_scope = False
    if host_ids:
        from app.services.scope_coverage import bulk_scope_coverage, project_has_any_scope
        scope_coverage_map = bulk_scope_coverage(
            db, project.id, host_ids, set(host_location_map.keys()),
        )
        project_has_scope = project_has_any_scope(db, project.id)

    # The kinds of evidence each listed host still lacks — the Evidence page's
    # gaps, host by host (`gap:<kind>` in the query lists them); one statement.
    from app.services.evidence_service import DOMAIN_LABELS, host_evidence_gaps
    evidence_gap_map = host_evidence_gaps(db, host_ids)

    serialized_hosts = []
    for host in hosts:
        # Review #5 — pass the windowed discoveries + aggregate note_count so
        # the base serializer never touches the (unloaded) notes/scan_history
        # relationships.
        serialized = _serialize_host_base(
            host, vuln_map.get(host.id),
            discoveries=discoveries_by_host.get(host.id, []),
            note_count=note_count_map.get(host.id, 0),
            host_scripts=[],
        )
        follow = follow_map.get(host.id)
        serialized["follow"] = _serialize_follow(follow) if follow else None

        # RV-8 — list-weight payload: script-free ports, no host_scripts.
        serialized["ports"] = [_serialize_port_light(p) for p in host.ports]

        serialized["planned_test_count"] = tp_count_map.get(host.id, 0)
        serialized["tested_record_count"] = te_count_map.get(host.id, 0)
        serialized["web_interface_count"] = wi_count_map.get(host.id, 0)
        serialized["netexec_result_count"] = netexec_count_map.get(host.id, 0)
        serialized["conflict_count"] = conflict_count_map.get(host.id, 0)
        serialized["finding_count"] = finding_count_map.get(host.id, 0)
        serialized["changed_recently"] = host.id in changed_map
        serialized["exploitable_count"] = exploit_count_map.get(host.id, 0)
        serialized["critical_exploitable_count"] = critical_exploit_count_map.get(host.id, 0)
        serialized["issue_counts"] = issue_count_map.get(host.id)
        serialized["weakness_flags"] = weakness_flag_map.get(host.id, [])
        serialized["check_ids"] = check_id_map.get(host.id, [])
        serialized["evidence_gaps"] = [
            {"key": key, "label": DOMAIN_LABELS[key]} for key in evidence_gap_map.get(host.id, [])
        ]
        _loc = host_location_map.get(host.id)
        serialized["primary_subnet"] = _loc["subnet"] if _loc else None
        serialized["primary_site"] = _loc["site"] if _loc else None
        serialized["scope_coverage"] = scope_coverage_map.get(host.id, "none")
        serialized["project_has_scope"] = project_has_scope
        serialized["other_reviewers"] = other_review_map.get(host.id, [])
        serialized["reviewed_by"] = reviewed_map.get(host.id, [])
        serialized["team_review_status"] = team_status_map.get(host.id)
        serialized["assignees"] = assignee_map.get(host.id, [])
        serialized["notes"] = [
            _serialize_note(note) for note in notes_by_host.get(host.id, [])
        ]
        serialized_hosts.append(serialized)

    return {
        "items": serialized_hosts,
        "total": total,
        "project_total": project_total,
        # What "select all matching" would reach, stated with the count it
        # is compared against (the bulk bar reads both from this answer).
        "bulk_select_cap": host_query.BULK_SELECT_CAP,
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
    #: The most ids this route returns (``host_query.BULK_SELECT_CAP``) — the
    #: cap that was applied, whether or not it cut this answer.
    cap: int


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
    even for large result sets.  ``cap`` is the bound that was applied
    (v2.476.0; the list's answer states the same number as
    ``bulk_select_cap``, so the page can say "the first N of M" up front).
    """
    cap = host_query.BULK_SELECT_CAP
    query = _build_filtered_host_query(
        db, current_user,
        **filters.as_builder_kwargs(),
        project_id=project.id,
    )
    total = query.with_entities(func.count(models.Host.id)).scalar() or 0
    # Ordered, so a capped answer is the same ids on every call (and the ones
    # a retry of the bulk action gets): the first by id.
    rows = (
        query.with_entities(models.Host.id)
        .order_by(models.Host.id)
        .limit(cap)
        .all()
    )
    ids = [r[0] for r in rows]
    return HostIdsResponse(ids=ids, total=total, capped=total > len(ids), cap=cap)


# The state boxes in the Hosts endpoint editor (open / closed / filtered, or
# "any"): every non-empty combination, keyed by its sorted states.
_EDITOR_PORT_STATES = ('closed', 'filtered', 'open')
_EDITOR_STATE_COMBOS = [
    combo for r in range(1, len(_EDITOR_PORT_STATES) + 1)
    for combo in itertools.combinations(_EDITOR_PORT_STATES, r)
]


def _state_counted_facet(db: Session, host_scope, value_col, applied_states, limit: int, *filters):
    """``[(value, count, state_counts)]`` for a port facet: distinct hosts per
    value under the applied states, and under each state combination the
    editor offers (``"open,closed"``…, ``"any"``). One grouped statement: the
    inner query reduces to one row per (value, host) with a flag per state."""
    def has(cond):
        return func.max(case((cond, 1), else_=0))

    applied = P.port_state_condition(applied_states) if applied_states else true()
    per_host = (
        db.query(
            value_col.label('v'),
            has(applied).label('m'),
            *[has(models.Port.state == s).label(s) for s in _EDITOR_PORT_STATES],
        )
        .join(models.Host, models.Host.id == models.Port.host_id)
        .filter(host_scope, *filters)
        .group_by(value_col, models.Port.host_id)
        .subquery()
    )
    any_hosts = func.count()
    combo_sums = [
        func.sum(case((or_(*[per_host.c[s] == 1 for s in combo]), 1), else_=0))
        for combo in _EDITOR_STATE_COMBOS
    ]
    rows = (
        db.query(per_host.c.v, any_hosts, func.sum(per_host.c.m), *combo_sums)
        .group_by(per_host.c.v)
        .order_by(any_hosts.desc(), per_host.c.v)
        .limit(limit)
        .all()
    )
    out = []
    for v, n_any, n_applied, *by_combo in rows:
        counts = {','.join(combo): int(n or 0) for combo, n in zip(_EDITOR_STATE_COMBOS, by_combo)}
        counts['any'] = int(n_any or 0)
        out.append((v, int(n_applied or 0), counts))
    return out


# review 2026-10-01 R18 — the facets that are aggregates over the project's
# scan data (ports, services, OS, technologies, weakness and check counts,
# RDAP owners) for the UNFILTERED Hosts page, kept per worker for a short time.
# The same pattern, bound and TTL as ``posture_service``'s cache: opening the
# Hosts page ran every one of these project-wide aggregations each time, and
# they change only when a scan is imported.  What a person edits and expects
# to see at once — tags, subnet labels, subnets, sites, the scan list — is NOT
# cached and is read on every request.
#
# Keyed by project and nothing else, which is safe because the unfiltered
# facets depend on nothing else: no filter is applied (so nothing of the
# caller's — ``assigned:me``, a follow status — enters), and the route gives
# every project role the same answer.  A filtered request never reads or
# writes it.  ``HOST_FACET_CACHE_SECONDS=0`` turns it off.
_FACET_CACHE_MAX = 256
_FACET_CACHE: Dict[int, "tuple[float, Dict[str, Any]]"] = {}


def _facet_cache_ttl() -> float:
    return float(settings.HOST_FACET_CACHE_SECONDS)


class _FacetScope:
    """The hosts the facets are counted over (review 2026-10-01 R18).

    With filters active, the matching host ids used to be a SUBQUERY embedded
    in each of the dozen facet statements, so the filter — a substring search,
    a DSL expression — was evaluated a dozen times per request.  Here it is
    evaluated ONCE: the ids are fetched and handed to every statement as one
    array parameter (``= ANY('{…}'::int[])`` — a single value, not one bind
    per id).  A facet counted "without its own dimension" reuses the same ids
    whenever that dimension is not filtered, which is the usual case.

    Off Postgres (the SQLite test fallback) there is no array type, and the
    subquery is embedded as before.
    """

    def __init__(self, db: Session, current_user: User, project: Project, filters: "HostFilterParams"):
        self.db = db
        self.current_user = current_user
        self.project = project
        self.kwargs = filters.as_builder_kwargs()
        self.has_filters = filters.active()
        self.ids: Optional[List[int]] = None
        if self.has_filters and db.get_bind().dialect.name == "postgresql":
            self.ids = [row[0] for row in self._matching(self.kwargs).all()]

    def _matching(self, kwargs: Dict[str, Any]):
        return _build_filtered_host_query(
            self.db, self.current_user, **kwargs, project_id=self.project.id,
        ).with_entities(models.Host.id)

    def _in_ids(self, column):
        from sqlalchemy import Integer, any_, cast, literal
        from sqlalchemy.dialects.postgresql import ARRAY

        packed = "{" + ",".join(str(i) for i in self.ids) + "}"
        return column == any_(cast(literal(packed), ARRAY(Integer)))

    def hosts(self):
        """A condition for a statement that has ``Host`` in it."""
        if not self.has_filters:
            return models.Host.project_id == self.project.id
        if self.ids is not None:
            return self._in_ids(models.Host.id)
        return models.Host.id.in_(self._matching(self.kwargs).scalar_subquery())

    def without(self, own_dimension: str, column, always: bool = False):
        """``column`` is a host matching every applied filter EXCEPT
        ``own_dimension`` — so choosing a value does not hide the ones not yet
        chosen.  With nothing else applied: ``None``, or with ``always`` the
        project's hosts."""
        kwargs = dict(self.kwargs)
        own_value = kwargs.get(own_dimension)
        kwargs[own_dimension] = None
        if all(v is None for v in kwargs.values()):
            if not always:
                return None
            return column.in_(
                self.db.query(models.Host.id).filter(models.Host.project_id == self.project.id)
            )
        if own_value is None and self.ids is not None:
            return self._in_ids(column)  # the same set: nothing was left out
        return column.in_(self._matching(kwargs))


def _scan_derived_facets(
    db: Session, current_user: User, project: Project, filters: "HostFilterParams", scope: _FacetScope,
) -> Dict[str, Any]:
    """The facets aggregated from scan data — the expensive part of
    ``/hosts/filters/data`` and the part its cache holds."""
    host_scope = scope.hosts()

    # Ports and services — scoped, one row per port NUMBER / service NAME,
    # counted in distinct hosts. Rows used to be split by (port, service, state)
    # and count port rows, so "2049 (nfs)" showed its largest slice and applying
    # port 2049 found more hosts (production 2026-09-26). Each row carries
    # `count` (hosts matching under the applied port_states — open unless it
    # names others) and `state_counts` (every combination the editor's state
    # boxes can make, plus "any"), so the picker shows the count for the states
    # being chosen before they are applied.
    port_filter_states = P.resolve_endpoint_states(
        (filters.port_states or '').split(','), has_endpoint=True,
    )
    port_rows = _state_counted_facet(
        db, host_scope, models.Port.port_number, port_filter_states, 500,
    )
    # Label each port with the service name most of those hosts report.
    port_service: Dict[int, tuple] = {}
    if port_rows:
        port_hosts = func.count(func.distinct(models.Port.host_id))
        for number, name, n in (
            db.query(models.Port.port_number, models.Port.service_name, port_hosts)
            .join(models.Host, models.Host.id == models.Port.host_id)
            .filter(
                host_scope,
                models.Port.port_number.in_([r[0] for r in port_rows]),
                models.Port.service_name.isnot(None), models.Port.service_name != '',
            )
            .group_by(models.Port.port_number, models.Port.service_name)
            .all()
        ):
            if number not in port_service or (n, name) > port_service[number]:
                port_service[number] = (n, name)
    port_state_label = ','.join(port_filter_states) if port_filter_states else 'any'

    services_result = _state_counted_facet(
        db, host_scope, models.Port.service_name, port_filter_states, 200,
        models.Port.service_name.isnot(None), models.Port.service_name != '',
    )

    # Operating systems — scoped
    os_query = db.query(
        models.Host.os_name,
        func.count(models.Host.id).label('count')
    ).filter(models.Host.os_name.isnot(None), models.Host.os_name != '')
    if host_scope is not None:
        os_query = os_query.filter(host_scope)
    # The name breaks ties (review 2026-10-01 R18): equal counts used to come
    # back in whatever order the plan produced, so the list — and which names
    # made the cut — could change between two identical requests.
    operating_systems = os_query.group_by(
        models.Host.os_name
    ).order_by(func.count(models.Host.id).desc(), models.Host.os_name).limit(100).all()

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

    # RDAP attribution facets — distinct owner / ASN / country across in-scope
    # hosts, each with a DISTINCT host count (a host maps to one block, but a
    # block covers many hosts).  Scoped by the active filter like the other
    # cascading facets.  Empty when the project has no RDAP data, which the
    # frontend reads as "hide this control".
    from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution

    def _attr_query(*entities):
        q = (
            db.query(*entities)
            .select_from(NetworkAttribution)
            .join(
                HostNetworkAttribution,
                HostNetworkAttribution.attribution_id == NetworkAttribution.id,
            )
            .join(models.Host, models.Host.id == HostNetworkAttribution.host_id)
        )
        return q.filter(host_scope) if host_scope is not None else q.filter(
            models.Host.project_id == project.id
        )

    distinct_hosts = func.count(func.distinct(HostNetworkAttribution.host_id))
    org_rows = (
        _attr_query(NetworkAttribution.org_name, distinct_hosts)
        .filter(NetworkAttribution.org_name.isnot(None), NetworkAttribution.org_name != '')
        .group_by(NetworkAttribution.org_name)
        .order_by(distinct_hosts.desc(), NetworkAttribution.org_name)
        .limit(200)
        .all()
    )
    asn_rows = (
        _attr_query(NetworkAttribution.asn, NetworkAttribution.as_name, distinct_hosts)
        .filter(NetworkAttribution.asn.isnot(None))
        .group_by(NetworkAttribution.asn, NetworkAttribution.as_name)
        .order_by(distinct_hosts.desc(), NetworkAttribution.asn, NetworkAttribution.as_name)
        .limit(200)
        .all()
    )
    country_rows = (
        _attr_query(NetworkAttribution.country, distinct_hosts)
        .filter(NetworkAttribution.country.isnot(None), NetworkAttribution.country != '')
        .group_by(NetworkAttribution.country)
        .order_by(distinct_hosts.desc(), NetworkAttribution.country)
        .all()
    )

    # v2.423.0 — weakness flags and checks.  Like subnets / sites, each is
    # counted under the OTHER applied conditions, so ticking one flag does not
    # zero the counts of the flags not yet ticked.  One statement each.
    from sqlalchemy import case
    weakness_counts = db.query(*[
        func.coalesce(func.sum(case(
            (weakness_predicate(db, current_user, project.id, [flag]), 1), else_=0,
        )), 0)
        for flag in WEAKNESS_FLAGS
    ]).filter(scope.without('weaknesses', models.Host.id, always=True)).one()
    descriptions = weakness_descriptions()
    weaknesses_result = [
        {'name': flag, 'label': WEAKNESS_LABELS[flag], 'description': descriptions[flag],
         'host_count': int(weakness_counts[i] or 0)}
        for i, flag in enumerate(WEAKNESS_FLAGS)
    ]

    from app.services.misconfig_checks import CHECKS
    check_hosts = func.count(func.distinct(Vulnerability.host_id))
    check_rows = (
        db.query(Vulnerability.check_id, check_hosts)
        .filter(Vulnerability.check_id.isnot(None),
                scope.without('checks', Vulnerability.host_id, always=True))
        .group_by(Vulnerability.check_id)
        .order_by(check_hosts.desc(), Vulnerability.check_id)
        .all()
    )
    checks_result = [
        {'id': cid, 'title': CHECKS[cid].title if cid in CHECKS else cid, 'host_count': int(n or 0)}
        for cid, n in check_rows
    ]

    return {
        'weaknesses': weaknesses_result,
        'checks': checks_result,
        'common_ports': [
            {'port': number, 'service': port_service.get(number, (0, 'unknown'))[1],
             'state': port_state_label, 'count': n, 'state_counts': by_state}
            for number, n, by_state in port_rows
        ],
        'services': [
            {'name': name, 'count': n, 'state_counts': by_state}
            for name, n, by_state in services_result
        ],
        'operating_systems': [
            {'name': o.os_name, 'count': o.count}
            for o in operating_systems
        ],
        'technologies': technologies_result,
        'orgs': [
            {'name': r[0], 'host_count': r[1] or 0}
            for r in org_rows
        ],
        'asns': [
            {'asn': r[0], 'as_name': r[1], 'host_count': r[2] or 0}
            for r in asn_rows
        ],
        'countries': [
            {'country': r[0], 'host_count': r[1] or 0}
            for r in country_rows
        ],
    }


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

    scope = _FacetScope(db, current_user, project, filters)

    derived: Optional[Dict[str, Any]] = None
    ttl = _facet_cache_ttl()
    cacheable = not scope.has_filters and ttl > 0
    now = time.monotonic()
    if cacheable:
        hit = _FACET_CACHE.get(project.id)
        if hit is not None and hit[0] > now:
            derived = hit[1]
    if derived is None:
        derived = _scan_derived_facets(db, current_user, project, filters, scope)
        if cacheable:
            if len(_FACET_CACHE) >= _FACET_CACHE_MAX:
                _FACET_CACHE.clear()  # crude bound; the cache is best-effort
            _FACET_CACHE[project.id] = (now + ttl, derived)

    # Scans — scoped to project so analysts see project-relevant scans
    scans = db.query(
        models.Scan.id, models.Scan.filename, models.Scan.tool_name,
        models.Scan.created_at, models.Scan.start_time, models.Scan.time_source
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

    # Subnets and sites come from the operator's scope, not from what the
    # filter matched, so every one is listed and the active filter only scopes
    # its count. They used to filter rows instead, which dropped any subnet or
    # site with no matching hosts: picking one subnet hid all the others, and a
    # filter nothing in scope matched (e.g. out-of-scope-only) emptied the
    # picker. Each facet's own dimension is left out of that filter so a
    # selection doesn't hide the values not yet picked. The project restriction
    # sits on the base relation (Scope.project_id — Subnet has no project_id);
    # v2.298.0 fixed a cross-project leak from it missing there.
    def _facet_mapping_join(own_dimension: str):
        join_cond = models.Subnet.id == models.HostSubnetMapping.subnet_id
        scoped = scope.without(own_dimension, models.HostSubnetMapping.host_id)
        return join_cond if scoped is None else and_(join_cond, scoped)

    subnet_count = func.count(models.HostSubnetMapping.id)
    subnets_result = (
        db.query(models.Subnet.cidr, subnet_count.label('host_count'))
        .join(models.Scope, models.Subnet.scope_id == models.Scope.id)
        .outerjoin(models.HostSubnetMapping, _facet_mapping_join('subnets'))
        .filter(models.Scope.project_id == project.id)
        .group_by(models.Subnet.id, models.Subnet.cidr)
        .order_by(subnet_count.desc(), models.Subnet.cidr)
        .limit(200)
        .all()
    )

    # COUNT DISTINCT host_id so a host in two subnets of the same site isn't
    # double-counted.
    sites_result = (
        db.query(
            models.Subnet.site,
            func.count(func.distinct(models.HostSubnetMapping.host_id)).label('host_count'),
        )
        .join(models.Scope, models.Subnet.scope_id == models.Scope.id)
        .outerjoin(models.HostSubnetMapping, _facet_mapping_join('sites'))
        .filter(
            models.Scope.project_id == project.id,
            models.Subnet.site.isnot(None),
            models.Subnet.site != '',
        )
        .group_by(models.Subnet.site)
        .order_by(models.Subnet.site)
        .all()
    )

    return {
        **derived,
        'subnets': [
            {'cidr': s.cidr, 'host_count': s.host_count or 0}
            for s in subnets_result
        ],
        'scans': [
            {
                'id': s.id, 'filename': s.filename, 'tool_name': s.tool_name,
                'created_at': s.created_at.isoformat() if s.created_at else None,
                # v2.333.0 — tz-tagged per time_source (app.services.scan_time).
                'start_time': (
                    scan_time_for_api(s.start_time, s.time_source).isoformat()
                    if s.start_time else None
                ),
                'time_source': s.time_source,
            }
            for s in scans
        ],
        'tags': tags_result,
        'subnet_labels': subnet_labels_result,
        'sites': [
            {'name': s.site, 'host_count': s.host_count or 0}
            for s in sites_result
        ],
    }


@router.get("/scan/{scan_id}", response_model=List[ScanHostSchema])
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

    # Query hosts through HostScanHistory.  v2.424.1 — only what ScanHost
    # carries is loaded: the host row and its ports.
    query = db.query(models.Host).options(
        selectinload(models.Host.ports),
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

    # v2.86.9 — port filter.  No state filter — operators often want to see
    # closed/filtered too while debugging coverage.
    #
    # v2.341.0 (review) — scoped to THIS scan through PortScanHistory.  The
    # filter used to read ``ports_v2`` alone, which is current state: a port
    # first seen by a later scan made this scan's view claim it observed the
    # port too.  ``PortScanHistory`` is the per-scan observation, so a port
    # counts here only if this scan recorded it.
    if port is not None:
        port_host_ids = (
            db.query(models.Port.host_id)
            .join(models.PortScanHistory, models.PortScanHistory.port_id == models.Port.id)
            .filter(
                models.Port.port_number == port,
                models.PortScanHistory.scan_id == scan_id,
            )
            .distinct()
        )
        query = query.filter(models.Host.id.in_(port_host_ids))

    # Apply pagination and return.  Ordered: OFFSET over an unordered join
    # can repeat a host on one page and miss it on the next.
    hosts = query.order_by(models.Host.id).offset(skip).limit(limit).all()
    return hosts



@router.get("/{host_id:int}", response_model=HostSchema)
def get_host_v2(
    host_id: int,
    include_info: bool = Query(
        False,
        description=(
            "Include severity-'info' vulnerability rows in `vulnerabilities` "
            "(v2.341.0). Off by default: on a Nessus host they are most of the "
            "rows and most of the payload, and the analytics ignore them. "
            "`informational_count` is always returned."
        ),
    ),
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
        selectinload(models.Host.scan_history).selectinload(models.HostScanHistory.scan),
        # Nothing below reads these three from the entity: the vulnerability
        # list is queried just under here WITH its informational filter,
        # notes come from the follow service, and the serializer never
        # touches attributes.  Redundant with the plain-lazy defaults; kept so
        # a stray read gets nothing rather than every row.
        noload(models.Host.vulnerabilities),
        noload(models.Host.attributes),
        noload(models.Host.notes),
    ).filter(models.Host.id == host_id, models.Host.project_id == project.id).first()

    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # v2.341.0 — the vulnerability list is loaded here, filtered, rather than
    # through the relationship: a Nessus host carries hundreds of informational
    # rows, each with its own copy of the plugin text, and the inspector shows
    # them only on request.  The count is cheap and always present.
    vuln_q = (
        db.query(Vulnerability)
        .options(
            selectinload(Vulnerability.port),
            # serialize_vulnerability reads vuln.promoted_findings for the "Promoted" badge.
            selectinload(Vulnerability.promoted_findings),
        )
        .filter(Vulnerability.host_id == host.id)
    )
    if not include_info:
        vuln_q = vuln_q.filter(Vulnerability.severity != VulnerabilitySeverity.INFO)
    host_vulnerabilities = vuln_q.all()
    informational_count = (
        db.query(func.count(Vulnerability.id))
        .filter(
            Vulnerability.host_id == host.id,
            Vulnerability.severity == VulnerabilitySeverity.INFO,
        )
        .scalar()
    ) or 0

    follow_service = HostFollowService(db)

    try:
        vulnerability_service = VulnerabilityService(db)
        # Count-only summary: the detail serializer reads only total +
        # by_severity from this (via build_vuln_summary); the vulnerability
        # list itself is ``host_vulnerabilities``, queried above.
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

    # Network provenance for this host, most-specific block first.
    from app.services.attribution_correlation import attributions_for_host

    # Which of this host's issues are already covered by a finding — one
    # query for the host, so the inspector can say "covered by finding N"
    # instead of offering a promote that would only re-attach evidence.
    from app.services.host_serialization import issue_coverage_map

    serialized = _serialize_host_detail(
        host, vuln_summary, follow_record, notes,
        attributions=attributions_for_host(db, host.id),
        vuln_coverage=issue_coverage_map(db, project.id, host_vulnerabilities, host_id=host.id),
        vulnerabilities=host_vulnerabilities,
        # Any row carrying ANY certificate fact; the serializer decides which
        # list each row belongs in (see host_detail_service.cert_web_interfaces).
        cert_web_interfaces=_cert_web_interfaces(db, host_id),
    )
    # v2.12.0: per-host count of web interfaces (httpx / eyewitness /
    # nikto rows).  HostDetail.tsx uses this to gate the "Web
    # Interfaces" card visibility — fetch the full list lazily
    # only when the count is > 0.
    # v2.362.0 — distinct (tool, URL), matching the list badge above and the
    # rows the section shows; the table keeps one row per scan.
    serialized["web_interface_count"] = (
        db.query(models.WebInterface.source, models.WebInterface.url)
        .filter(models.WebInterface.host_id == host_id)
        .distinct()
        .count()
    )
    # v2.45.7 — gate the HostInspector NetExec card the same way as
    # the Web Interfaces card: a cheap count, full rows fetched lazily.
    serialized["netexec_result_count"] = (
        db.query(func.count(NetexecResult.id))
        .filter(NetexecResult.host_id == host_id)
        .scalar()
    ) or 0
    # v2.390.0 — distinct URLs content discovery found (one row per scan).
    serialized["web_path_count"] = (
        db.query(func.count(distinct(models.WebPath.url)))
        .filter(models.WebPath.host_id == host_id)
        .scalar()
    ) or 0
    # v2.341.0 — how many informational rows exist and whether this response
    # carries them, so the inspector can offer "N informational · show".
    serialized["informational_count"] = informational_count
    serialized["informational_included"] = include_info
    # v2.342.0 — which scope entries cover this host (subnets by mapping,
    # names by current resolution), so the inspector can list them instead
    # of leaving "is this in scope, and why" to the Hosts-list column.
    from app.services.scope_coverage import host_scope_membership

    serialized["scope_membership"] = host_scope_membership(db, host)
    # v2.348.0 — freshness per assessment domain, so the inspector can say
    # "observed yesterday, vulnerabilities not assessed, tested 3 months ago"
    # instead of one last-seen for everything.
    from app.services.host_assessment_service import host_assessment
    serialized["assessment"] = host_assessment(db, host)
    # Owner/assignee enrichment — the base detail serializer leaves this []
    # (it needs a user join), so mirror the list endpoint here.  Without this
    # the inspector can't show or manage the host's owner. (1.2b)
    serialized["assignees"] = _host_assignees(db, host_id)
    # v2.423.0 — the weakness / access flags, so the inspector states the
    # ones a filter matched (an EOL OS read as a plain OS name).
    serialized["weakness_flags"] = host_weakness_flags(db, current_user, project.id, [host_id]).get(host_id, [])
    serialized["weakness_labels"] = {f: WEAKNESS_LABELS[f] for f in serialized["weakness_flags"]}
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

    # Conflict history — the object_type/object_id shape the panel reads,
    # assembled by the shared service (the agent host detail uses it too).
    conflicts = _host_conflict_history(db, host)

    return {
        "conflict_count": _host_conflict_counts(db, [host_id]).get(host_id, 0),
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
    project_total = (
        db.query(func.count(models.DNSRecord.id))
        .filter(models.DNSRecord.project_id == project.id)
        .scalar()
    ) or 0
    return HostDnsRecordsResponse(
        items=items,
        total=len(items),
        resolvers=resolvers,
        record_types=record_types,
        project_total=project_total,
    )


# Tool-ready caps (module-level so tests can shrink them). IP-only formats
# stream in chunks of TOOL_READY_STREAM_CHUNK and are never capped; formats
# that load the Host entity with its port/script graph stop at
# TOOL_READY_ENTITY_CAP (v2.90.1: a 42k-host eager load OOM-killed a worker).
TOOL_READY_STREAM_CHUNK = 5_000
TOOL_READY_ENTITY_CAP = 50_000


@router.get(
    "/tool-ready/{format}",
    # Data egress (scanner-target lists) — same policy as /export and /reports:
    # VIEWERs read the inventory but cannot export it; AUDITOR and above may.
    # Route-level (not router-level) because the rest of /hosts is viewer-readable.
    dependencies=[Depends(require_project_role(ProjectRole.AUDITOR))],
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
    names_scope: str = Query(
        "in_scope",
        pattern="^(in_scope|all)$",
        description=(
            "For the name-aware formats (names, web-targets, nuclei, json): which names "
            "currently bound to the selected hosts to use — only names covered by a declared "
            "domain (default) or every bound name."
        ),
    ),
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
    - **nuclei** — Nuclei target format: URLs for web ports, IP for the rest.
      v2.330.0: URLs are built from the names CURRENTLY bound to the address
      (one URL per name — the vhost behind a load balancer is the target,
      not the shared address); the IP URL is the fallback when no bound
      name qualifies.  Previously always the IP.
    - **host-port** — IP:PORT for each open port
    - **json** — JSON array with host details (``names`` = currently bound
      names alongside the legacy single ``hostname``)
    - **names** — one FQDN per line: every name currently bound to a selected
      host, deduplicated, wildcards excluded (patterns are not targets)
    - **web-targets** — ``scheme://<fqdn>[:port]`` per (bound name, open web
      port) for hosts with web ports; IP URL when the host has no bound name

    ``names_scope`` (default ``in_scope``) restricts the name-aware formats
    to names a declared domain covers; ``all`` uses every bound name.  "Bound"
    is ``dns_name_service.current_binding_condition`` — the one "currently
    resolves to" rule — so a name that moved away never lingers as a target.

    v2.93.0 — converged onto the shared ``HostFilterParams`` bundle, so
    this export now honours every Hosts-page filter (tags, labels,
    web/tech, the ``has:*`` family, and the ``q`` boolean query) instead
    of the partial subset it accepted before.
    """

    # Validate format
    supported_formats = ['ip-list', 'nmap', 'metasploit', 'masscan', 'nuclei', 'host-port', 'json', 'names', 'web-targets']
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
    _NEEDS_PORT_DATA = {"host-port", "nuclei", "web-targets"}
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

    # Formats that never load a Host entity are not capped: a field project
    # needed all 96,542 addresses and got the first 50,000, and the dialog
    # never said so. The IP formats read one column and stream the matching
    # set in id-keyset chunks, so memory stays at one chunk however many hosts
    # match; they also drop the list's default sort, a correlated vuln-count
    # ORDER BY over every host that buys nothing in a target list. ``names``
    # reads only ids before one bound-names query per 1000 hosts.
    #
    # The entity-loading formats keep TOOL_READY_ENTITY_CAP. Truncation is
    # signalled in response headers, never the body — that must stay clean
    # for ``nmap -iL tool-ready.txt``.
    total_count = query.order_by(None).with_entities(func.count(models.Host.id)).scalar() or 0
    content_type, filename = _get_content_type_and_filename(format)
    response_headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "X-Tool-Ready-Total": str(total_count),
    }

    _IP_ONLY_JOINERS = {"ip-list": "\n", "nmap": " ", "metasploit": " ", "masscan": ","}
    in_scope_names_only = names_scope != "all"
    if format in _IP_ONLY_JOINERS:
        joiner = _IP_ONLY_JOINERS[format]
        id_ip = query.order_by(None).with_entities(models.Host.id, models.Host.ip_address)

        def _stream_ips():
            last_id, started = 0, False
            while True:
                rows = (
                    id_ip.filter(models.Host.id > last_id)
                    .order_by(models.Host.id)
                    .limit(TOOL_READY_STREAM_CHUNK)
                    .all()
                )
                if not rows:
                    return
                ips = [ip for _, ip in rows if ip]
                if ips:
                    yield (joiner if started else "") + joiner.join(ips)
                    started = True
                last_id = rows[-1][0]
                if len(rows) < TOOL_READY_STREAM_CHUNK:
                    return

        response_headers["X-Tool-Ready-Returned"] = str(total_count)
        return StreamingResponse(_stream_ips(), media_type=content_type, headers=response_headers)

    if format == "names":
        # One ordered DISTINCT query, streamed. Names outnumber hosts (several
        # bind to one address), so the per-host shape this replaced — a host-id
        # list, a host→names dict, a dedupe set, then one joined string — was
        # the last unbounded path here once the cap came off. The database does
        # the dedupe and the sort; a server-side cursor keeps the response at
        # one chunk in memory.
        from app.services import dns_name_service as _dns
        # One statement sorts every name before the first row is streamed, and
        # the response has started by the time it could fail: a streamed
        # export is exempt from the API statement timeout (review 2026-10-01
        # R23).  The IP formats above read in short keyset chunks and are not.
        disable_statement_timeout(db)
        r = aliased(models.DNSRecord)
        n = models.DNSName
        matching_hosts = query.order_by(None).with_entities(models.Host.id).scalar_subquery()
        # Codepoint order, matching the Python sort this replaced — a locale
        # collation orders punctuation (dots, hyphens) differently. SELECT
        # DISTINCT requires the ORDER BY expression to be the selected one, so
        # the collation rides on the column itself.
        _bind_dialect = db.get_bind().dialect.name if db.get_bind() is not None else "postgresql"
        fqdn_col = n.fqdn.collate("C") if _bind_dialect == "postgresql" else n.fqdn
        names_q = (
            db.query(fqdn_col.label("fqdn"))
            .select_from(models.Host)
            .join(r, and_(r.project_id == project.id, r.value == models.Host.ip_address))
            .join(n, n.id == r.name_id)
            .filter(
                models.Host.id.in_(matching_hosts),
                n.project_id == project.id,
                n.kind == "fqdn",  # a wildcard is a pattern, never a target
                _dns.current_binding_condition(r),
            )
        )
        if in_scope_names_only:
            names_q = names_q.filter(_dns.name_in_scope_condition(project.id))
        names_q = names_q.distinct().order_by(fqdn_col)

        def _stream_names():
            batch: List[str] = []
            started = False
            for (fqdn,) in names_q.yield_per(TOOL_READY_STREAM_CHUNK):
                batch.append(fqdn)
                if len(batch) >= TOOL_READY_STREAM_CHUNK:
                    yield ("\n" if started else "") + "\n".join(batch)
                    started, batch = True, []
            if batch:
                yield ("\n" if started else "") + "\n".join(batch)

        response_headers["X-Tool-Ready-Returned"] = str(total_count)
        return StreamingResponse(_stream_names(), media_type=content_type, headers=response_headers)
    else:
        # Host-id order, as the streamed IP formats use: without an ORDER BY
        # both the file's order and WHICH hosts survive the cap were whatever
        # the plan returned (a test flaked on it).
        hosts = query.order_by(models.Host.id).limit(TOOL_READY_ENTITY_CAP).all()
        hosts_returned = len(hosts)
        if total_count > TOOL_READY_ENTITY_CAP:
            response_headers["X-Tool-Ready-Truncated"] = "true"
            response_headers["X-Tool-Ready-Limit"] = str(TOOL_READY_ENTITY_CAP)
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
        names_by_host = (
            _current_names_for_hosts(
                db, project.id, [h.id for h in hosts], in_scope_only=in_scope_names_only,
            )
            if format in _NAME_AWARE_FORMATS
            else {}
        )
        output = _generate_tool_output(hosts, format, include_ports, output_filters, names_by_host)

    response_headers["X-Tool-Ready-Returned"] = str(hosts_returned)
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

    # v2.403.0 — the query's rule: a port/service condition keeps OPEN ports
    # unless a state was named (`any` = every state).
    port_state_values = P.resolve_endpoint_states(
        (filters.get("port_states") or "").split(","),
        has_endpoint=bool(port_values or service_values),
    )
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


# Formats whose output uses the names currently bound to each address.
_NAME_AWARE_FORMATS = {"nuclei", "json", "names", "web-targets"}

# The web-port table nuclei always used; web-targets and the name-preferring
# nuclei share it so the two can never disagree on what counts as "web".
_WEB_PORTS = (80, 443, 8000, 8080, 8081, 8008, 8443, 8444, 8888)
_TLS_PORTS = (443, 8443, 8444)


def _current_names_for_hosts(
    db: Session, project_id: int, host_ids: List[int], *, in_scope_only: bool,
) -> Dict[int, List[str]]:
    """``host_id -> [fqdn, ...]`` (sorted) for the names CURRENTLY bound to
    each host's address — ``current_binding_condition`` is the one rule, so an
    address a name moved away from is never a target.  Concrete names only
    (a wildcard is a pattern, not a target); ``in_scope_only`` additionally
    requires a declared domain to cover the name.  One query per 1000 hosts,
    never one per host."""
    from app.services import dns_name_service as _dns

    out: Dict[int, List[str]] = {}
    if not host_ids:
        return out
    r = aliased(models.DNSRecord)
    n = models.DNSName
    for start in range(0, len(host_ids), 1000):
        chunk = host_ids[start:start + 1000]
        q = (
            db.query(models.Host.id, n.fqdn)
            .join(r, and_(r.project_id == project_id, r.value == models.Host.ip_address))
            .join(n, n.id == r.name_id)
            .filter(
                models.Host.id.in_(chunk),
                n.project_id == project_id,
                n.kind == "fqdn",
                _dns.current_binding_condition(r),
            )
        )
        if in_scope_only:
            q = q.filter(_dns.name_in_scope_condition(project_id))
        for hid, fqdn in q.distinct().all():
            out.setdefault(hid, []).append(fqdn)
    for hid in out:
        out[hid].sort()
    return out


def _web_urls_for_host(
    host: models.Host, filters: Optional[Dict[str, Optional[str | bool]]], names: List[str],
) -> List[str]:
    """URLs for the host's open web ports: one per (bound name, port), or the
    IP URL per port when no bound name qualifies.  Empty when the host has no
    open web port."""
    web_ports = [
        p.port_number
        for p in _get_filtered_output_ports(host, filters)
        if p.state == 'open' and p.port_number in _WEB_PORTS
    ]
    if not web_ports:
        return []
    targets = names or [host.ip_address]
    urls: List[str] = []
    for port_num in web_ports:
        scheme = 'https' if port_num in _TLS_PORTS else 'http'
        suffix = '' if port_num in (80, 443) else f":{port_num}"
        for t in targets:
            urls.append(f"{scheme}://{t}{suffix}")
    return urls


def _generate_tool_output(
    hosts: List[models.Host],
    format: str,
    include_ports: bool = False,
    filters: Optional[Dict[str, Optional[str | bool]]] = None,
    names_by_host: Optional[Dict[int, List[str]]] = None,
) -> str:
    names_by_host = names_by_host or {}
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
        # Nuclei target format — URLs for web ports (by bound name, IP
        # fallback — see _web_urls_for_host), bare IP for non-web hosts.
        targets = []
        for host in hosts:
            urls = _web_urls_for_host(host, filters, names_by_host.get(host.id, []))
            targets.extend(urls if urls else [host.ip_address])
        return '\n'.join(targets)

    elif format == 'web-targets':
        # Only hosts with an open web port; one URL per (bound name, port).
        targets = []
        for host in hosts:
            targets.extend(_web_urls_for_host(host, filters, names_by_host.get(host.id, [])))
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
                # Names currently bound to this address (per names_scope);
                # `hostname` above is the legacy single display name.
                'names': names_by_host.get(host.id, []),
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
        'json': ('application/json', 'hosts.json'),
        'names': ('text/plain', 'names.txt'),
        'web-targets': ('text/plain', 'web-targets.txt'),
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
    # v2.323.0 — the named endpoint this interface was reached as (URL
    # hostname when it's a name); null when the URL targeted an IP literal.
    name_id: Optional[int] = None
    fqdn: Optional[str] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    status_code: Optional[int] = None
    title: Optional[str] = None
    server_header: Optional[str] = None
    content_length: Optional[int] = None
    technologies: Optional[List[str]] = None
    favicon_hash: Optional[str] = None
    tls_info: Optional[dict] = None
    # Typed cert / TLS promotions (surfaced per-port in the host inspector).
    cert_not_after: Optional[datetime] = None
    cert_self_signed: Optional[bool] = None
    cert_subject_org: Optional[str] = None
    tls_weak_protocol: Optional[bool] = None
    has_screenshot: bool = False
    # v2.390.0 — the page text EyeWitness captured (stored, never served).
    page_text: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    scan_id: int
    port_id: Optional[int] = None
    # v2.364.0 (code review finding 21) — WHEN this was observed, as distinct
    # from when the row was written.  ``first_seen`` / ``last_seen`` are the
    # database's clock: ``first_seen`` is the import time and ``last_seen``
    # moves on every row update, and no parser sets either.  Since the
    # inspector shows ONE row per (tool, URL), ranking by ``last_seen`` let an
    # old scan imported later — or any metadata touch — become "the latest".
    #   observed_at        the scan's own end (else start) time, when the tool
    #                      recorded one — emitted by the one scan-time rule
    #                      (services/scan_time), so a zone-less tool clock
    #                      stays naive;
    #   observed_at_basis  "scan" when that time exists, else "import": all
    #                      that is known is when the file was uploaded, and
    #                      ``observed_at`` is then ``first_seen``.
    observed_at: Optional[datetime] = None
    observed_at_basis: str = "import"
    scan_filename: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class WebPathResponse(BaseModel):
    """One path content discovery found on this host (v2.390.0), the latest
    observation of its URL, and how many scans reported it."""
    url: str
    path: str
    status_code: Optional[int] = None
    size: Optional[int] = None
    source: str
    port: Optional[int] = None
    last_seen: Optional[datetime] = None
    scans: int = 1


@router.get(
    "/{host_id:int}/web-paths",
    response_model=List[WebPathResponse],
    summary="Paths found by content discovery (ffuf / gobuster / feroxbuster / dirsearch / dirbuster)",
)
def list_host_web_paths(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    host = (
        db.query(models.Host.id)
        .filter(models.Host.id == host_id, models.Host.project_id == project.id)
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    latest: Dict[str, Any] = {}
    counts: Dict[str, int] = {}
    for row, port_number in (
        db.query(models.WebPath, models.Port.port_number)
        .outerjoin(models.Port, models.Port.id == models.WebPath.port_id)
        .filter(models.WebPath.host_id == host_id)
        .order_by(models.WebPath.id)
        .all()
    ):
        counts[row.url] = counts.get(row.url, 0) + 1
        latest[row.url] = (row, port_number)
    return sorted(
        (
            WebPathResponse(
                url=r.url, path=r.path, status_code=r.status_code, size=r.size, source=r.source,
                port=p, last_seen=r.first_seen, scans=counts[r.url],
            )
            for r, p in latest.values()
        ),
        key=lambda w: (w.port or 0, w.path),
    )


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
    # When each row was OBSERVED — the one rule shared with the agent read
    # (services/web_interface_observation).
    from app.services.web_interface_observation import observations
    observed = observations(db, rows)

    return [
        WebInterfaceResponse(
            id=r.id,
            source=r.source,
            url=r.url,
            name_id=r.name_id,
            fqdn=r.name.fqdn if r.name else None,
            protocol=r.protocol,
            port=r.port,
            status_code=r.status_code,
            title=r.title,
            server_header=r.server_header,
            content_length=r.content_length,
            technologies=r.technologies or [],
            favicon_hash=r.favicon_hash,
            page_text=(r.page_text or None) and r.page_text[:2000],
            tls_info=r.tls_info,
            cert_not_after=r.cert_not_after,
            cert_self_signed=r.cert_self_signed,
            cert_subject_org=r.cert_subject_org,
            tls_weak_protocol=r.tls_weak_protocol,
            has_screenshot=bool(r.screenshot_path),
            first_seen=r.first_seen,
            last_seen=r.last_seen,
            scan_id=r.scan_id,
            port_id=r.port_id,
            observed_at=observed[r.id].observed_at,
            observed_at_basis=observed[r.id].basis,
            scan_filename=observed[r.id].scan_filename,
        )
        for r in rows
    ]


class WebInterfaceRecordResponse(BaseModel):
    """The tool's own record behind one web interface (v2.417.0)."""
    id: int
    source: str
    url: str
    scan_filename: Optional[str] = None
    # Pretty-printed JSON of what the parser kept (`web_interfaces.raw`).
    text: Optional[str] = None
    total_chars: int = 0
    truncated: bool = False


# Enough for any single tool record seen so far (a full testssl target is
# ~60 KB pretty-printed); a larger one is cut and says so.
WEB_RECORD_LIMIT_CHARS = 500_000


@router.get(
    "/web-interfaces/{interface_id:int}/record",
    response_model=WebInterfaceRecordResponse,
    summary="The stored source record behind one web interface",
)
def get_web_interface_record(
    interface_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """httpx, WhatWeb, testssl and EyeWitness keep each record they read
    (`raw`), but only chosen fields were served: testssl's OK/INFO checks and
    cipher lists, WhatWeb's plugin strings and httpx's DNS/CDN/redirect data
    were stored and unreachable (review 2026-09-25 R08).  Fetched on demand,
    one record at a time — never with the list."""
    import json

    row = (
        db.query(models.WebInterface.id, models.WebInterface.source, models.WebInterface.url,
                 models.WebInterface.raw, models.Scan.filename)
        .outerjoin(models.Scan, models.Scan.id == models.WebInterface.scan_id)
        .filter(models.WebInterface.id == interface_id, models.WebInterface.project_id == project.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Web interface not found")
    text = None if row.raw is None else json.dumps(row.raw, indent=2, ensure_ascii=False, default=str)
    total = len(text or "")
    return WebInterfaceRecordResponse(
        id=row.id, source=row.source, url=row.url, scan_filename=row.filename,
        text=text[:WEB_RECORD_LIMIT_CHARS] if text else None,
        total_chars=total, truncated=total > WEB_RECORD_LIMIT_CHARS,
    )


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
    hostname: Optional[str] = None
    domain_name: Optional[str] = None
    # `shares` is parser-shaped JSON (the netexec output varies); the
    # frontend renders it defensively.
    shares: Optional[Any] = None
    first_seen: Optional[datetime] = None
    # v2.390.0 — which tool (netexec | smbmap), "(Pwn3d!)" and SMBv1.
    tool: str = "netexec"
    local_admin: Optional[bool] = None
    smbv1: Optional[bool] = None
    # v2.411.0 — the tool's own line.  Only the SMB banner and login lines are
    # interpreted; LDAP / RDP / VNC flags and module output live only here, so
    # it is shown rather than kept invisibly.
    raw_output: Optional[str] = None
    # v2.417.0 — the parser keeps the first 10 000 characters of a result's
    # output; True when it had to cut, so the UI says the line is partial.
    raw_output_truncated: bool = False

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
            hostname=r.hostname,
            domain_name=r.domain_name,
            shares=r.shares,
            # NetexecResult model carries `discovered_at` (server_default
            # = func.now() when the row is inserted), not `first_seen`.
            # The response schema field is named `first_seen` for parity
            # with the WebInterface response above — both surface "when
            # did we first observe this artefact" to the analyst.  Pre-
            # fix this attribute access raised AttributeError and 500'd
            # the netexec card.
            first_seen=r.discovered_at,
            tool=r.tool or "netexec",
            local_admin=r.local_admin,
            smbv1=r.smbv1,
            # Credentials that worked are what an analyst looks for here: the
            # line is shown as the tool wrote it.  v2.417.0 — whole, as
            # stored: the API cut it at 2 000 characters with no marker, so
            # module output past that point was unreachable.
            raw_output=r.raw_output or None,
            raw_output_truncated=len(r.raw_output or "") >= NETEXEC_RAW_OUTPUT_LIMIT,
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

    require_readable_file(target, "Screenshot")

    return FileResponse(
        path=str(target),
        media_type="image/png",
        filename=os.path.basename(str(target)),
    )


# ---------------------------------------------------------------------------
