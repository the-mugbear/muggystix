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
from app.db.models import HostFollow, HostNote as HostNoteModel
from app.db.models_vulnerability import Vulnerability
from app.api.v1.endpoints.host_follow import _serialize_follow
from app.api.v1.endpoints.host_notes import _serialize_note
from app.schemas.schemas import HostVulnerabilitySummary


# Ranking used when ordering vulnerabilities within a host's payload.
# Lower number = higher priority on display.
SEVERITY_ORDER = {
    'critical': 0,
    'high': 1,
    'medium': 2,
    'low': 3,
    'info': 4,
    'unknown': 5,
}


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


def serialize_host_base(host: models.Host, vuln_data: Optional[dict]) -> dict:
    """Common host fields used by both the list and the detail endpoints."""
    note_count = len(getattr(host, "notes", []))
    history_entries = sorted(
        list(getattr(host, "scan_history", []) or []),
        key=lambda entry: entry.discovered_at or datetime.min,
        reverse=True,
    )

    discoveries: List[dict] = []
    seen_scan_ids = set()
    for history in history_entries:
        if history.scan_id in seen_scan_ids:
            continue
        seen_scan_ids.add(history.scan_id)
        scan = getattr(history, "scan", None)
        # Surface the scan's actual time window in addition to the row's
        # ingest timestamp.  `discovered_at` is when we INGESTED the
        # observation (often hours/days after the scan ran); `start_time`
        # and `end_time` are when the tool was actually probing the
        # network.  SOC alert correlation needs the latter — analysts
        # ask "what was running at 14:32?", not "what got uploaded?".
        discoveries.append({
            "scan_id": history.scan_id,
            "scan_filename": getattr(scan, "filename", None) if scan else None,
            "scan_type": getattr(scan, "scan_type", None) if scan else None,
            "tool_name": getattr(scan, "tool_name", None) if scan else None,
            "scan_start": getattr(scan, "start_time", None) if scan else None,
            "scan_end": getattr(scan, "end_time", None) if scan else None,
            "command_line": getattr(scan, "command_line", None) if scan else None,
            "discovered_at": history.discovered_at,
        })

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


def serialize_host_detail(
    host: models.Host,
    vuln_data: Optional[dict],
    follow: Optional[HostFollow],
    notes: List[HostNoteModel],
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
    deserialized values vary between Postgres and SQLite paths.
    """
    severity = None
    if vuln.severity:
        try:
            severity = vuln.severity.value  # type: ignore[assignment]
        except AttributeError:
            severity = str(vuln.severity).lower()

    source = None
    if getattr(vuln, "source", None):
        try:
            source = vuln.source.value  # type: ignore[attr-defined]
        except AttributeError:
            source = str(vuln.source).lower()

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
