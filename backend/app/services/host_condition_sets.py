"""Per-condition host-id sets — the single source of truth for "which hosts in
this project exhibit weakness X".

Two surfaces read these and must never disagree:

* **Systemic insights** (``systemic_insight_service``) intersects each set with
  its in-scope host set and measures how widely the weakness spreads.
* **The /hosts filter DSL** (``has:eol`` / ``has:smb_unsigned`` /
  ``has:weak_auth`` / ``has:cert_issue`` / ``has:cleartext``) turns the same
  sets into a drill-down so an analyst can jump from "SMB signing is an estate
  blind spot — 40 hosts" straight to those 40 hosts.

Keeping the judgments here once means the manager's headline count and the
analyst's drill-down are literally the same computation.  The non-trivial bits
— the EOL regex catalog, latest-observation-wins for TLS certs and NetExec auth
— live here so they can't drift between the two views.

Each function returns the set of ``Host.id`` for EVERY host in the project that
exhibits the condition.  Callers restrict further: systemic ∩ in-scope; the DSL
``AND``\\s the result with the user's other predicates.  The simple column
conditions (SMB signing, cleartext ports) are *also* expressed directly in SQL
by ``host_query_predicates`` for index-friendly filtering at scale — those are
trivially equivalent to the queries here (same column / same port set), so there
is no judgment to drift; this module owns the shared ``CLEARTEXT_PORTS`` const.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import WebInterface
from app.db.models_confidence import NetexecResult
from app.services.cert_fields import cert_issue_from_columns
from app.services.os_eol import match_eol_os
from app.services.subnet_insight_service import _EPOCH, _is_weak_user, _normalize_dt

# Cleartext-credential ports (credentials observable on the wire) —
# Telnet / FTP / POP3 / IMAP.  Shared so the DSL predicate and the systemic
# view filter the identical port set.
CLEARTEXT_PORTS = {21, 23, 110, 143}


def eol_os_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts whose ``os_name`` matches the end-of-life OS catalog."""
    return {
        hid
        for hid, os_name in db.query(models.Host.id, models.Host.os_name)
        .filter(models.Host.project_id == project_id)
        .all()
        if match_eol_os(os_name) is not None
    }


def smb_unsigned_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts whose recorded SMB-signing posture is ``disabled``."""
    return {
        hid
        for (hid,) in db.query(models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            models.Host.smb_signing == "disabled",
        )
        .all()
    }


def cleartext_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts with at least one OPEN cleartext-credential port."""
    return {
        hid
        for (hid,) in db.query(models.Port.host_id)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            models.Port.state == "open",
            models.Port.port_number.in_(CLEARTEXT_PORTS),
        )
        .distinct()
        .all()
    }


def cert_issue_host_ids(
    db: Session, project_id: int, now: Optional[datetime] = None
) -> Set[int]:
    """Hosts whose LATEST cert observation per (host, url) is expired or
    self-signed.

    Latest-observation-wins so a stale expired cert that has since been
    re-observed clean does not outlive the fix — mirrors the subnet-insights
    hygiene lens exactly.
    """
    now = now or datetime.now(timezone.utc)
    cert_latest: Dict[Tuple[int, Optional[str]], Tuple] = {}
    for hid, url, not_after, self_signed, last_seen in (
        db.query(
            WebInterface.host_id,
            WebInterface.url,
            WebInterface.cert_not_after,
            WebInterface.cert_self_signed,
            WebInterface.last_seen,
        )
        .filter(
            WebInterface.project_id == project_id,
            WebInterface.host_id.isnot(None),
            or_(
                WebInterface.cert_not_after.isnot(None),
                WebInterface.cert_self_signed.isnot(None),
            ),
        )
        .all()
    ):
        ls = _normalize_dt(last_seen) or _EPOCH
        prev = cert_latest.get((hid, url))
        if prev is None or ls >= prev[0]:
            cert_latest[(hid, url)] = (ls, not_after, self_signed)
    out: Set[int] = set()
    for (hid, _url), (_ls, not_after, self_signed) in cert_latest.items():
        if cert_issue_from_columns(not_after, self_signed, now):
            out.add(hid)
    return out


def weak_auth_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts where the LATEST NetExec observation per (host, proto, port) is a
    successful guest / anonymous / null-session login.

    ``_is_weak_user`` only flags an EXPLICIT weak identity — an unknown
    username never counts against a host.
    """
    nxc_latest: Dict[Tuple[int, Optional[str], Optional[int]], Tuple] = {}
    for hid, proto, port, auth_success, username, discovered_at in (
        db.query(
            NetexecResult.host_id,
            NetexecResult.protocol,
            NetexecResult.port,
            NetexecResult.auth_success,
            NetexecResult.username,
            NetexecResult.discovered_at,
        )
        .join(models.Host, NetexecResult.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id)
        .all()
    ):
        d = _normalize_dt(discovered_at) or _EPOCH
        prev = nxc_latest.get((hid, proto, port))
        if prev is None or d >= prev[0]:
            nxc_latest[(hid, proto, port)] = (d, auth_success, username)
    out: Set[int] = set()
    for (hid, _p, _pt), (_d, auth_success, username) in nxc_latest.items():
        if auth_success and _is_weak_user(username):
            out.add(hid)
    return out
