"""
Host serialization helpers — extracted from hosts.py in v2.27.0.

The /hosts/ endpoint family returns a few related response shapes
(`HostListResponse`, `HostSchema`, the conflicts response) and the
field-by-field translation from ORM rows to dicts was inline in the
route file.  Moving it here keeps the route handlers focused on HTTP
concerns and lets the serializer paths be exercised in isolation.

These are pure functions: no FastAPI deps, no session deps; they
take ORM rows + side-data and return dicts ready for Pydantic
validation.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from app.db import models
from app.db.models import HostFollow, Annotation as AnnotationModel
from app.db.models_vulnerability import Vulnerability, enum_value, SEVERITY_KEYS
from app.schemas.schemas import HostVulnerabilitySummary, Annotation, HostFollowInfo


# CR4-2 — these two ORM->schema mappers used to live in the host_follow /
# host_notes routers and were imported back into this service, making a
# service depend on routers.  They are pure serialization, so they belong
# here; the routers now import them from this module.

def _serialize_follow(follow: HostFollow) -> HostFollowInfo:
    return HostFollowInfo(
        status=follow.status,
        last_viewed_at=follow.last_viewed_at,
        created_at=follow.created_at,
        updated_at=follow.updated_at,
    )


def _serialize_note(note: AnnotationModel) -> Annotation:
    author_name = None
    if note.author:
        author_name = note.author.full_name or note.author.username
    assignee_name = None
    if note.assignee:
        assignee_name = note.assignee.full_name or note.assignee.username
    return Annotation(
        id=note.id,
        body=note.body,
        status=note.status,
        author_id=note.user_id,
        author_name=author_name,
        parent_id=note.parent_id,
        assignee_id=note.assignee_id,
        assignee_name=assignee_name,
        due_at=note.due_at,
        note_type=note.note_type,
        resolution_summary=note.resolution_summary,
        pinned=bool(note.pinned),
        # If this thread root has been promoted, surface the finding id so the
        # UI shows a "promoted" badge + link (and can warn on re-promote).
        finding_id=(note.promoted_findings[0].id if note.promoted_findings else None),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


# Ranking used when ordering vulnerabilities within a host's payload.
# Lower number = higher priority on display.  Derived from the canonical
# SEVERITY_KEYS so the bucket ordering has one source (critical=0 … unknown=5).
SEVERITY_ORDER = {sev: i for i, sev in enumerate(SEVERITY_KEYS)}


def build_vuln_summary(data: Optional[dict]) -> Optional[HostVulnerabilitySummary]:
    """Translate the host's denormalized `vuln_summary` JSON into the
    typed response shape.  Returns ``None`` when the host has no
    vulnerabilities so the field can be omitted cleanly."""
    if not data or data.get('total', 0) == 0:
        return None
    return HostVulnerabilitySummary(
        total_vulnerabilities=data.get('total', 0),
        critical=data.get('by_severity', {}).get('critical', 0),
        high=data.get('by_severity', {}).get('high', 0),
        medium=data.get('by_severity', {}).get('medium', 0),
        low=data.get('by_severity', {}).get('low', 0),
        info=data.get('by_severity', {}).get('info', 0),
    )


def discovery_dict(history) -> dict:
    """One ``discoveries[]`` entry from a HostScanHistory row (+ its scan).

    ``discovered_at`` is the INGEST time; ``scan_start``/``scan_end`` are when
    the tool was actually probing — what SOC-alert correlation needs.
    """
    scan = getattr(history, "scan", None)
    return {
        "scan_id": history.scan_id,
        "scan_filename": getattr(scan, "filename", None) if scan else None,
        "scan_type": getattr(scan, "scan_type", None) if scan else None,
        "tool_name": getattr(scan, "tool_name", None) if scan else None,
        "scan_start": getattr(scan, "start_time", None) if scan else None,
        "scan_end": getattr(scan, "end_time", None) if scan else None,
        "command_line": getattr(scan, "command_line", None) if scan else None,
        "discovered_at": history.discovered_at,
    }


def serialize_host_base(
    host: models.Host,
    vuln_data: Optional[dict],
    *,
    discoveries: Optional[List[dict]] = None,
    note_count: Optional[int] = None,
) -> dict:
    """Common host fields used by both the list and the detail endpoints.

    ``discoveries`` / ``note_count`` may be supplied precomputed (the LIST
    endpoint passes windowed top-N + aggregate counts so it never has to
    eager-load every note/scan-history row — review #5).  When omitted, they
    are derived from the loaded relationships (the detail endpoint's path).
    """
    if note_count is None:
        note_count = len(getattr(host, "notes", []) or [])
    if discoveries is None:
        history_entries = sorted(
            list(getattr(host, "scan_history", []) or []),
            key=lambda entry: entry.discovered_at or datetime.min,
            reverse=True,
        )
        discoveries = []
        seen_scan_ids = set()
        for history in history_entries:
            if history.scan_id in seen_scan_ids:
                continue
            seen_scan_ids.add(history.scan_id)
            discoveries.append(discovery_dict(history))

    # Tags attached to this host.  ``tag_assignments`` is selectin-loaded;
    # callers that show tags should also eager-load ``tag_assignments.tag``
    # to avoid an N+1 on the .tag lookup below.
    tags = []
    for assignment in getattr(host, "tag_assignments", []) or []:
        tag = getattr(assignment, "tag", None)
        if tag is not None:
            tags.append({"id": tag.id, "name": tag.name, "color": tag.color})
    tags.sort(key=lambda t: (t["name"] or "").lower())

    return {
        "id": host.id,
        "ip_address": host.ip_address,
        "hostname": host.hostname,
        "state": host.state,
        "state_reason": host.state_reason,
        "os_name": host.os_name,
        "os_family": host.os_family,
        "os_generation": host.os_generation,
        "os_type": host.os_type,
        "os_vendor": host.os_vendor,
        "os_accuracy": host.os_accuracy,
        "last_updated_scan_id": host.last_updated_scan_id,
        "first_seen": host.first_seen,
        "last_seen": host.last_seen,
        "ports": host.ports,
        "host_scripts": host.host_scripts,
        "vulnerability_summary": build_vuln_summary(vuln_data),
        "vulnerabilities": [],
        "note_count": note_count,
        "tags": tags,
        # Populated by the list/detail endpoints (needs a user join);
        # default empty so the base serializer stays pure.
        "assignees": [],
        "discoveries": discoveries,
    }


# RV-8 — the Hosts LIST renders a row summary, not the drill-down: it
# shows up to 3 notes and 6 discoveries and never the NSE script bodies.
# These helpers/caps let the list endpoint return ports WITHOUT script
# output (the single largest payload contributor) and bounded discoveries,
# so a page of hosts with long scan histories / verbose NSE no longer ships
# drill-down-sized object graphs.  Detail keeps the full payload.
LIST_DISCOVERY_CAP = 6


def serialize_port_light(port: models.Port) -> dict:
    """Port row for the LIST view — service/state columns only, NO scripts.

    Built from already-loaded columns; never touches ``port.scripts`` so it
    can't trigger a lazy-load N+1 once the list query drops that eager-load.
    """
    return {
        "id": port.id,
        "host_id": port.host_id,
        "port_number": port.port_number,
        "protocol": port.protocol,
        "state": port.state,
        "reason": port.reason,
        "service_name": port.service_name,
        "service_product": port.service_product,
        "service_version": port.service_version,
        "service_extrainfo": port.service_extrainfo,
        "service_method": port.service_method,
        "service_conf": port.service_conf,
        "last_updated_scan_id": port.last_updated_scan_id,
        "scripts": [],
    }


def serialize_host_detail(
    host: models.Host,
    vuln_data: Optional[dict],
    follow: Optional[HostFollow],
    notes: List[AnnotationModel],
) -> dict:
    """Detail-endpoint payload — base + follow state + notes +
    ordered vulnerabilities."""
    serialized = serialize_host_base(host, vuln_data)
    serialized["follow"] = _serialize_follow(follow) if follow else None
    serialized["notes"] = [_serialize_note(note) for note in notes]
    serialized["note_count"] = len(notes)

    vulnerabilities = sorted(
        getattr(host, "vulnerabilities", []) or [],
        key=vulnerability_sort_key,
    )
    serialized["vulnerabilities"] = [
        serialize_vulnerability(vuln) for vuln in vulnerabilities
    ]
    return serialized


def serialize_vulnerability(vuln: Vulnerability) -> dict:
    """Translate one Vulnerability row into the response dict.

    Handles both the enum and string representations of ``severity``
    and ``source`` because the columns are SQL enums but the
    deserialized values vary between Postgres and SQLite paths —
    :func:`enum_value` collapses both to the canonical lowercase string.
    """
    severity = enum_value(vuln.severity) if vuln.severity else None
    source = enum_value(getattr(vuln, "source", None)) or None

    # v2.45.6 — `references` is stored as a Text column holding a JSON
    # array of URLs/identifiers.  Parse it to a real list for the API.
    # Defensive: tolerate a plain string, malformed JSON, or NULL — a
    # bad references blob must never break the whole host detail load.
    references: List[str] = []
    raw_refs = getattr(vuln, "references", None)
    if raw_refs:
        try:
            parsed = json.loads(raw_refs)
            if isinstance(parsed, list):
                references = [str(r).strip() for r in parsed if str(r).strip()]
            elif isinstance(parsed, str) and parsed.strip():
                references = [parsed.strip()]
        except (ValueError, TypeError):
            # Not JSON — treat the raw text as a single reference.
            references = [str(raw_refs).strip()]

    port_number = None
    protocol = None
    service_name = None
    if vuln.port:
        port_number = vuln.port.port_number
        protocol = vuln.port.protocol
        service_name = vuln.port.service_name

    return {
        "id": vuln.id,
        "plugin_id": vuln.plugin_id,
        "title": vuln.title,
        "severity": severity,
        "source": source,
        "cvss_score": vuln.cvss_score,
        "cvss_vector": vuln.cvss_vector,
        "cve_id": vuln.cve_id,
        "scan_id": vuln.scan_id,
        "port_id": vuln.port_id,
        "port_number": port_number,
        "protocol": protocol,
        "service_name": service_name,
        "exploitable": vuln.exploitable,
        # If promoted to a finding, surface its id so the vuln row shows a
        # "Promoted" badge + guards a duplicate promote.
        "finding_id": (vuln.promoted_findings[0].id if vuln.promoted_findings else None),
        "first_seen": vuln.first_seen,
        "last_seen": vuln.last_seen,
        "solution": vuln.solution,
        # v2.45.6 — these three were stored by the parsers but dropped
        # at this serializer, so the UI could never show the actual
        # vulnerability writeup or its references.
        "description": vuln.description,
        "references": references,
        "source_plugin_name": vuln.source_plugin_name,
    }


def vulnerability_sort_key(vuln: Vulnerability) -> tuple:
    """Order vulnerabilities for display: severity first (critical →
    info), then most-recently-seen, then id for a stable tail."""
    severity_value = None
    if vuln.severity:
        try:
            severity_value = vuln.severity.value  # type: ignore[assignment]
        except AttributeError:
            severity_value = str(vuln.severity).lower()

    severity_rank = SEVERITY_ORDER.get(severity_value or 'unknown', SEVERITY_ORDER['unknown'])
    last_seen_dt = vuln.last_seen or vuln.first_seen
    if last_seen_dt is None:
        last_seen_dt = datetime.utcfromtimestamp(0)
    return (severity_rank, -last_seen_dt.timestamp(), vuln.id)
