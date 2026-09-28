"""Correct NetExec rows stored before v2.428.4 by the rules the parser uses now.

Two parser bugs left wrong rows behind (MCP acceptance run, agent feedback
#12 / #15):

* a ``[+]`` / ``[-]`` line that does not start with a credential — an
  ``ERROR(MANTIS\\SQLEXPRESS): …``, a module's ``VULNERABLE`` — was stored as a
  login, with a username cut from the message;
* nxc's ``None`` in the name column (no name) became a host called "None".

Re-importing does not fix them (a re-import is a new scan; the old rows stay),
so this re-reads each stored row's own line with the parser's rule.  It never
invents data: a row is only cleared, never re-attributed.  Idempotent.

``repair_nfs_mount_ports`` (v2.430.1) renames the mount daemon's ports that
NetExec stored as "nfs"; see its docstring.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult
from app.parsers.netexec_parser import NFS_PORT, _PASSWORD_ONLY_PROTOCOLS, _is_credential

_STATUS_DETAIL = re.compile(r"\[([+\-])\]\s+(.*)")
# What nxc prints for "no name" (the parser's _NO_NAME), as it is stored.
_NO_NAME_VALUES = ["None", "NONE", "none", "null", "NULL", "-"]


def _not_a_login(row: NetexecResult) -> bool:
    if row.auth_success is None or (row.protocol or "").lower() in _PASSWORD_ONLY_PROTOCOLS:
        return False
    first_line = (row.raw_output or "").splitlines()[0] if row.raw_output else ""
    m = _STATUS_DETAIL.search(first_line)
    return bool(m) and not _is_credential(m.group(2))


def repair_netexec_results(db: Session, *, project_id: Optional[int] = None, apply: bool = False) -> Dict[str, int]:
    """Counts of what is (or, without ``apply``, would be) corrected."""
    counts = {"logins_cleared": 0, "host_names_cleared": 0, "result_names_cleared": 0}

    rows = db.query(NetexecResult).filter(NetexecResult.auth_success.isnot(None))
    hosts = db.query(models.Host).filter(models.Host.hostname.isnot(None))
    if project_id is not None:
        rows = rows.join(models.Host, models.Host.id == NetexecResult.host_id).filter(
            models.Host.project_id == project_id)
        hosts = hosts.filter(models.Host.project_id == project_id)

    for row in rows.all():
        if _not_a_login(row):
            counts["logins_cleared"] += 1
            if apply:
                row.auth_success = None
                row.username = None
                row.local_admin = None

    for host in hosts.filter(models.Host.hostname.in_(_NO_NAME_VALUES)).all():
        counts["host_names_cleared"] += 1
        if apply:
            host.hostname = None

    named = db.query(NetexecResult).filter(NetexecResult.hostname.in_(_NO_NAME_VALUES))
    if project_id is not None:
        named = named.join(models.Host, models.Host.id == NetexecResult.host_id).filter(
            models.Host.project_id == project_id)
    for row in named.all():
        counts["result_names_cleared"] += 1
        if apply:
            row.hostname = None

    if apply:
        db.flush()
    return counts


def repair_nfs_mount_ports(db: Session, *, project_id: Optional[int] = None, apply: bool = False) -> Dict[str, int]:
    """v2.430.1 — ports stored before then as an "nfs" service that are the
    NFS MOUNT DAEMON's.  nxc logs its NFS lines with mountd's dynamic port
    (nxc/protocols/nfs.py ``create_conn_obj``), and the parser named every
    such port "nfs" (production 2026-09-28: ~700 of them from one import).

    A port is renamed ``mountd`` only when nothing but NetExec named it: a TCP
    port other than 2049, called "nfs" with no confident identification (an
    nmap ``-sV`` name is never touched), and "nfs" in no other tool's record
    of it.  NetExec's own per-scan records of those ports are renamed too.
    Idempotent."""
    from sqlalchemy import func, or_

    counts = {"nfs_ports_renamed_mountd": 0, "nfs_port_records_renamed": 0, "nfs_ports_kept_other_tool": 0}
    is_netexec = func.lower(func.coalesce(models.Scan.tool_name, models.Scan.scan_type, "")) == "netexec"

    ports = (
        db.query(models.Port)
        .filter(
            models.Port.service_name == "nfs",
            models.Port.port_number != NFS_PORT,
            models.Port.protocol == "tcp",
            or_(models.Port.service_conf.is_(None), models.Port.service_conf == 0),
        )
    )
    if project_id is not None:
        ports = ports.join(models.Host, models.Host.id == models.Port.host_id).filter(
            models.Host.project_id == project_id)
    candidates = {p.id: p for p in ports.all()}
    if not candidates:
        return counts

    # Who called each candidate "nfs": NetExec, another tool, or both.
    named_by: Dict[int, set] = {}
    ids = list(candidates)
    for start in range(0, len(ids), 1000):
        rows = (
            db.query(models.PortScanHistory.port_id, is_netexec)
            .join(models.Scan, models.Scan.id == models.PortScanHistory.scan_id)
            .filter(models.PortScanHistory.port_id.in_(ids[start:start + 1000]),
                    models.PortScanHistory.service_name == "nfs")
            .all()
        )
        for port_id, from_netexec in rows:
            named_by.setdefault(port_id, set()).add(bool(from_netexec))

    for port_id, port in candidates.items():
        sources = named_by.get(port_id, set())
        if sources != {True}:
            # Another tool said "nfs" too — or no record says who did.
            counts["nfs_ports_kept_other_tool"] += 1
            continue
        counts["nfs_ports_renamed_mountd"] += 1
        records = (
            db.query(models.PortScanHistory)
            .join(models.Scan, models.Scan.id == models.PortScanHistory.scan_id)
            .filter(models.PortScanHistory.port_id == port_id,
                    models.PortScanHistory.service_name == "nfs", is_netexec)
        )
        for record in records.all():
            counts["nfs_port_records_renamed"] += 1
            if apply:
                record.service_name = "mountd"
        if apply:
            port.service_name = "mountd"

    if apply:
        db.flush()
    return counts
