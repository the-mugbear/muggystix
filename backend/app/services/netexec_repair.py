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
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult
from app.parsers.netexec_parser import _PASSWORD_ONLY_PROTOCOLS, _is_credential

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
