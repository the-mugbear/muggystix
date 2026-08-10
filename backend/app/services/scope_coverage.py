"""Which hosts fall outside every scope CIDR in a project.

"Out of scope" is a *derived* property, not a stored one: a host is out of
scope when subnet correlation found no ``host_subnet_mappings`` row for it,
i.e. its address doesn't fall inside any subnet of any scope on the project.

This module exists because that fact previously had two implementations. The
export endpoint computed it correctly from ``hosts_v2``; the JSON listing
endpoint read the ``out_of_scope_hosts`` table, which host deduplication (the
move to one ``Host`` row per IP per project) stopped writing. Nothing has
written that table since, so the listing endpoint answered "no hosts are out
of scope" for every project — a confident wrong answer rather than a visible
failure, and one that agents consume through the same API surface operators
do.

Both callers now share the query below, so the two can't drift apart again.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import models

# Constant, because the derivation admits exactly one reason. It's carried on
# every row so a consumer (an agent especially) can tell *why* a host is
# listed without having to know how the endpoint is implemented.
OUT_OF_SCOPE_REASON = (
    "No subnet mapping — the address falls outside every scope CIDR "
    "defined on this project."
)


def _base_query(db: Session, project_id: int):
    mapped = select(models.HostSubnetMapping.id).where(
        models.HostSubnetMapping.host_id == models.Host.id
    )
    return (
        db.query(models.Host)
        .filter(models.Host.project_id == project_id)
        .filter(~mapped.exists())
    )


def out_of_scope_hosts(
    db: Session,
    project_id: int,
    *,
    search: Optional[str] = None,
    skip: Optional[int] = None,
    limit: Optional[int] = None,
) -> Tuple[List[models.Host], int]:
    """Return ``(hosts, total)`` for hosts with no scope mapping.

    ``total`` reflects the search filter, so narrowing the search narrows both
    the rows and the count. Pass ``skip``/``limit`` to page; omit both to get
    every row (the export path, which streams a whole project).
    """
    q = _base_query(db, project_id)

    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        q = q.filter(
            or_(
                models.Host.ip_address.ilike(like),
                models.Host.hostname.ilike(like),
            )
        )

    total = q.with_entities(func.count(models.Host.id)).scalar() or 0

    q = q.order_by(models.Host.ip_address.asc())
    if skip:
        q = q.offset(skip)
    if limit is not None:
        q = q.limit(limit)

    return q.all(), total
