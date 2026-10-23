"""NetExec / SMBMap results on one host — one read for both doors.

The inspector's NetExec card (``GET /hosts/{id}/netexec``) and the agents'
``GET /agent/assist/hosts/{id}/access`` (MCP ``assist_list_host_access``) had a
query and a serializer each: different orders, the agent's rows lacked the
host name, domain and first-seen time the page shows, and the page's lacked
``writable_share`` (review 2026-10-23b, AGT-5).  Both call :func:`page` and serialize with :func:`serialize`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models_confidence import NETEXEC_RAW_OUTPUT_LIMIT, NetexecResult


class HostAccessResult(BaseModel):
    """One NetExec / SMBMap result on a host, as stored: what the parser read
    from the tool's line (login outcome, local admin, SMBv1, shares) beside the
    line itself, so a reader can check one against the other."""
    id: int
    scan_id: int
    #: netexec | smbmap
    tool: str = "netexec"
    protocol: str
    port: Optional[int] = None
    auth_success: Optional[bool] = None
    username: Optional[str] = None
    hostname: Optional[str] = None
    domain_name: Optional[str] = None
    # Parser-shaped JSON (the tool's output varies); readers render it defensively.
    shares: Optional[Any] = None
    #: When the result was first recorded (the row's ``discovered_at``).
    first_seen: Optional[datetime] = None
    local_admin: Optional[bool] = None
    smbv1: Optional[bool] = None
    writable_share: Optional[bool] = None
    # The tool's own line, whole as stored.  Only the SMB banner and login lines
    # are interpreted; LDAP / RDP / VNC flags and module output live only here.
    raw_output: Optional[str] = None
    #: True when the parser had to cut the line at its storage limit.
    raw_output_truncated: bool = False


def page(
    db: Session, host_id: int, *, limit: Optional[int] = None, offset: int = 0,
) -> Tuple[List[NetexecResult], int]:
    """The host's results in one order (protocol, port, id) and their total;
    ``limit=None`` is every row (the inspector's card).  The caller has checked
    that the host is in its project."""
    scoped = db.query(NetexecResult).filter(NetexecResult.host_id == host_id)
    total = scoped.with_entities(func.count(NetexecResult.id)).scalar() or 0
    q = scoped.order_by(NetexecResult.protocol, NetexecResult.port, NetexecResult.id).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q.all(), total


def serialize(r: NetexecResult) -> HostAccessResult:
    return HostAccessResult(
        id=r.id,
        scan_id=r.scan_id,
        tool=r.tool or "netexec",
        protocol=r.protocol,
        port=r.port,
        auth_success=r.auth_success,
        username=r.username,
        hostname=r.hostname,
        domain_name=r.domain_name,
        shares=r.shares,
        first_seen=r.discovered_at,
        local_admin=r.local_admin,
        smbv1=r.smbv1,
        writable_share=r.writable_share,
        # Credentials that worked are what an analyst looks for here: the line
        # is shown as the tool wrote it.
        raw_output=r.raw_output or None,
        raw_output_truncated=len(r.raw_output or "") >= NETEXEC_RAW_OUTPUT_LIMIT,
    )
