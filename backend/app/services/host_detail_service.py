"""Host-detail facts shared by the host inspector (``GET /hosts/{id}``) and the
agent's ``GET /agent/assist/hosts/{id}`` (v2.428.0).

These lived as private helpers in ``api/v1/endpoints/hosts.py``; an agent
answering "who owns this host / where did scans disagree / which findings are
live on it" has to state the same numbers the inspector shows, so the logic
moved here and both routes call it.  A service never imports a router
(``tests/test_service_router_boundary.py``).
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import HostFollow
from app.db.models_auth import User
from app.db.models_confidence import ConflictHistory


def host_assignees(db: Session, host_id: int) -> list:
    """Assignees for a single host — the detail equivalent of the Hosts list's
    batched ``assignee_map``.  A ``HostFollow`` with a non-null
    ``assigned_at`` means the host is assigned to that user."""
    rows = (
        db.query(
            HostFollow.user_id,
            HostFollow.assigned_at,
            HostFollow.assigned_by_id,
            User.username,
            User.full_name,
        )
        .join(User, HostFollow.user_id == User.id)
        .filter(HostFollow.host_id == host_id, HostFollow.assigned_at.isnot(None))
        .all()
    )
    return [
        {
            "user_id": uid,
            "name": full_name or username,
            "assigned_at": assigned_at,
            "assigned_by_id": assigned_by_id,
        }
        for uid, assigned_at, assigned_by_id, username, full_name in rows
    ]


def host_conflict_counts(db: Session, host_ids: List[int]) -> Dict[int, int]:
    """Canonical per-host data-conflict count → the number of HOST-level
    ``ConflictHistory`` rows (each is a recorded disagreement where a later
    scan's value displaced a prior value on one of the host's fields, e.g. one
    scan said OS=Linux, another OS=Windows).

    Single source of truth: the Hosts-list ``conflict_count`` badge, the
    host-detail conflicts pane and the agent host detail all call this, so
    they cannot diverge.  (They used to: the list counted ``ConflictHistory``
    rows while the pane counted *confidence records* — a different table,
    including port-level rows — so the same host showed e.g. 14 vs 12.)
    """
    if not host_ids:
        return {}
    return dict(
        db.query(ConflictHistory.host_id, func.count(ConflictHistory.id))
        .filter(ConflictHistory.host_id.in_(host_ids))
        .group_by(ConflictHistory.host_id)
        .all()
    )


_HOST_FIELDS = {"state", "os_name", "hostname"}


def host_conflict_history(
    db: Session, host: models.Host, *, host_limit: int = 100, port_limit: int = 10
) -> List[dict]:
    """The recorded disagreements on a host and its ports, newest first.

    ``host_limit`` is 100, not 10: ``conflict_count`` counts every host-level
    row, and a badge reading "14 conflicts" over a list of 10 is a count its
    drill-down cannot account for.

    v2.367.0 — each row carries what a reader needs to STATE the
    disagreement instead of pointing at scan ids: each scan's filename, and
    the value the host holds today (a conflict is recorded whether or not the
    reported value was adopted, so "previous → new" alone does not say which
    one won).
    """
    host_conflicts = (
        db.query(ConflictHistory)
        .filter(ConflictHistory.host_id == host.id)
        .order_by(ConflictHistory.resolved_at.desc())
        .limit(host_limit)
        .all()
    )
    port_ids = db.query(models.Port.id).filter(models.Port.host_id == host.id).scalar_subquery()
    port_conflicts = (
        db.query(ConflictHistory)
        .filter(ConflictHistory.port_id.in_(port_ids))
        .order_by(ConflictHistory.resolved_at.desc())
        .limit(port_limit)
        .all()
    )
    scan_ids = {
        sid for c in host_conflicts + port_conflicts
        for sid in (c.previous_scan_id, c.new_scan_id) if sid is not None
    }
    scan_names = dict(
        db.query(models.Scan.id, models.Scan.filename)
        .filter(models.Scan.id.in_(scan_ids)).all()
    ) if scan_ids else {}

    out = []
    for conflict in host_conflicts + port_conflicts:
        is_host = conflict.host_id is not None
        current = (
            getattr(host, conflict.field_name, None)
            if is_host and conflict.field_name in _HOST_FIELDS else None
        )
        out.append({
            "previous_scan_filename": scan_names.get(conflict.previous_scan_id),
            "new_scan_filename": scan_names.get(conflict.new_scan_id),
            "current_value": str(current) if current is not None else None,
            "id": conflict.id,
            "object_type": "host" if is_host else "port",
            "object_id": conflict.host_id if is_host else conflict.port_id,
            "field_name": conflict.field_name,
            "previous_value": conflict.previous_value,
            "previous_confidence": conflict.previous_confidence,
            "previous_scan_id": conflict.previous_scan_id,
            "previous_method": conflict.previous_method,
            "new_value": conflict.new_value,
            "new_confidence": conflict.new_confidence,
            "new_scan_id": conflict.new_scan_id,
            "new_method": conflict.new_method,
            "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
        })
    return out


def active_finding_counts(db: Session, host_ids: List[int]) -> Dict[int, int]:
    """Count of ACTIVE findings per host (foundation 6d) — the Hosts-list
    finding badge.  "Active" = the finding is still being worked AND this
    host's own endpoint is live: a host dismissed as a false positive (or
    recorded remediated) on a finding kept counting it (review 2026-09-23 R7).
    One grouped query."""
    if not host_ids:
        return {}
    from app.db.models_findings import (
        ACTIVE_FINDING_STATUSES, Finding, FindingHost, finding_active_on_host,
    )
    return {
        hid: cnt
        for hid, cnt in (
            db.query(FindingHost.host_id, func.count(func.distinct(Finding.id)))
            .join(Finding, Finding.id == FindingHost.finding_id)
            .filter(
                FindingHost.host_id.in_(host_ids),
                Finding.status.in_(ACTIVE_FINDING_STATUSES),
                finding_active_on_host(),
            )
            .group_by(FindingHost.host_id)
            .all()
        )
    }


def cert_web_interfaces(db: Session, host_id: int, limit: int = 5) -> list:
    """The host's web-interface rows that carry ANY certificate fact, most
    recent first — the input of ``host_serialization.serialize_cert_facts``.

    Queried by host_id: Host has no ``web_interfaces`` relationship (it lives
    on Scan).  v2.244.0 — widened from ``cert_subject_org IS NOT NULL``: a DV
    certificate (Let's Encrypt et al.) carries no organizationName, so every
    such host was dropped before its expiry / self-signed fields were read.
    """
    return (
        db.query(models.WebInterface)
        .filter(
            models.WebInterface.host_id == host_id,
            or_(
                models.WebInterface.cert_subject_org.isnot(None),
                models.WebInterface.cert_not_after.isnot(None),
                models.WebInterface.cert_self_signed.isnot(None),
            ),
        )
        .order_by(models.WebInterface.last_seen.desc().nullslast())
        .limit(limit)
        .all()
    )
