"""Host-detail facts shared by the host inspector (``GET /hosts/{id}``) and the
agent's ``GET /agent/assist/hosts/{id}`` (v2.428.0).

These lived as private helpers in ``api/v1/endpoints/hosts.py``; an agent
answering "who owns this host / where did scans disagree / which findings are
live on it" has to state the same numbers the inspector shows, so the logic
moved here and both routes call it.  A service never imports a router
(``tests/test_service_router_boundary.py``).
"""
from __future__ import annotations

from typing import Dict, List, Optional

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


def conflicts_per_host(
    db: Session, *, host_ids: Optional[List[int]] = None, project_id: Optional[int] = None,
):
    """``(hid, n_conf)`` subquery: per host, its recorded disagreements — the
    rows on the host's own fields AND on its ports' (owner, 2026-10-23: a
    port conflict is the host's).  A ``ConflictHistory`` row sets exactly one
    of ``host_id`` / ``port_id``; a port row reaches its host through
    ``ports_v2``.  Two indexed branches (``idx_conflict_history_host`` /
    ``_port``) UNION ALL'd, never a ``coalesce`` the indexes cannot serve.

    Narrowed to ``host_ids`` (the list, the inspector) or to the hosts of
    ``project_id`` (the investigation queue's tier 5)."""
    Port = models.Port

    def narrowed(q, host_col):
        if host_ids is not None:
            q = q.filter(host_col.in_(host_ids))
        if project_id is not None:
            q = q.join(models.Host, models.Host.id == host_col).filter(models.Host.project_id == project_id)
        return q

    on_host = narrowed(
        db.query(ConflictHistory.host_id.label("hid"), ConflictHistory.id.label("cid")),
        ConflictHistory.host_id,
    )
    on_port = narrowed(
        db.query(Port.host_id.label("hid"), ConflictHistory.id.label("cid"))
        .join(Port, Port.id == ConflictHistory.port_id),
        Port.host_id,
    )
    rows = on_host.union_all(on_port).subquery("conflict_rows")
    return (
        db.query(rows.c.hid.label("hid"), func.count(rows.c.cid).label("n_conf"))
        .group_by(rows.c.hid)
        .subquery("conflict_sig")
    )


def host_conflict_counts(db: Session, host_ids: List[int]) -> Dict[int, int]:
    """Canonical per-host data-conflict count: every ``ConflictHistory`` row
    on the host or on one of its ports (``conflicts_per_host``) — each a
    recorded disagreement where a later scan reported a different value than
    the one held, e.g. one scan said OS=Linux, another OS=Windows, or two
    scans named a port's service differently.

    Single source of truth: the Hosts-list ``conflict_count`` badge, the
    host-detail conflicts pane, the host assessment's ``conflicts`` and the
    agent host detail all call this, and the investigation queue's tier 5
    ("Scans disagree") ranks on the same subquery, so they cannot diverge.
    (The assessment used to count port rows while the others did not — one
    inspector, two numbers; review 2026-10-23b, INV-1.)
    """
    if not host_ids:
        return {}
    sig = conflicts_per_host(db, host_ids=list(host_ids))
    return {int(hid): int(n) for hid, n in db.query(sig.c.hid, sig.c.n_conf).all()}


_HOST_FIELDS = {"state", "os_name", "hostname"}


def host_conflict_history(db: Session, host: models.Host, *, limit: int = 100) -> List[dict]:
    """The recorded disagreements on a host and its ports, newest first — the
    rows ``host_conflict_counts`` counts, in one list under one cap, so the
    pane lists what its count says (a reader is told how many the cap left
    out: the count minus the rows).

    v2.367.0 — each row carries what a reader needs to STATE the
    disagreement instead of pointing at scan ids: each scan's filename, and
    the value the host holds today (a conflict is recorded whether or not the
    reported value was adopted, so "previous → new" alone does not say which
    one won).
    """
    port_ids = db.query(models.Port.id).filter(models.Port.host_id == host.id).scalar_subquery()
    conflicts = (
        db.query(ConflictHistory)
        .filter(or_(ConflictHistory.host_id == host.id, ConflictHistory.port_id.in_(port_ids)))
        # The id breaks a tie: one import records several conflicts in one
        # instant, and which of them the cap kept was the planner's choice.
        .order_by(ConflictHistory.resolved_at.desc(), ConflictHistory.id.desc())
        .limit(limit)
        .all()
    )
    scan_ids = {
        sid for c in conflicts
        for sid in (c.previous_scan_id, c.new_scan_id) if sid is not None
    }
    scan_names = dict(
        db.query(models.Scan.id, models.Scan.filename)
        .filter(models.Scan.id.in_(scan_ids)).all()
    ) if scan_ids else {}

    out = []
    for conflict in conflicts:
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
