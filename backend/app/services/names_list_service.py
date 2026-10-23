"""The names inventory's list: ONE filtered, ordered query behind the Names
page (``GET /projects/{id}/names``), its export and the agents'
``GET /agent/assist/names`` (MCP ``assist_list_names``).

Review 2026-10-23, R-AG3: the agents' read re-assembled the page's query from
the same predicates and lacked the ``shared`` state; it lived in the router
(``dns_names._names_query``), where another router may not import it.
"""
from __future__ import annotations

from typing import Literal, Optional, get_args

from sqlalchemy.orm import Session

from app.db import models
from app.services import dns_name_service as svc
from app.services.host_query_common import escape_like

#: The page's state chips, one word each.  A route types its ``state``
#: argument with it, so an unknown word is a 422 naming the accepted values on
#: every door and the OpenAPI (and so the MCP tool) carries the enum.
NameState = Literal["all", "unresolved", "resolved", "in_scope", "out_of_scope", "wildcard", "shared"]
NAME_STATES = get_args(NameState)

NameSort = Literal["fqdn", "last_seen", "first_seen"]
SortOrder = Literal["asc", "desc"]


def apply_name_state(q, state: str, project_id: int):
    """Narrow a query over ``DNSName`` to one state."""
    n = models.DNSName
    if state == "unresolved":
        return q.filter(~svc.resolving_exists_condition())
    if state == "resolved":
        return q.filter(svc.resolving_exists_condition())
    if state == "in_scope":
        return q.filter(svc.name_in_scope_condition(project_id))
    if state == "out_of_scope":
        return q.filter(~svc.name_in_scope_condition(project_id))
    if state == "wildcard":
        return q.filter(n.kind == "wildcard")
    if state == "shared":
        return q.filter(svc.shared_address_condition(project_id))
    if state != "all":
        raise ValueError(f"state must be one of {', '.join(NAME_STATES)}")
    return q


def names_query(
    db: Session,
    project_id: int,
    *,
    search: Optional[str] = None,
    state: str = "all",
    sort: str = "fqdn",
    order: str = "asc",
):
    """The ``(DNSName, in_scope)`` query, filtered and ordered — so the list,
    the export and the agents' read can never cover a different set than the
    page the operator is looking at."""
    n = models.DNSName
    in_scope = svc.name_in_scope_condition(project_id).label("in_scope")
    q = db.query(n, in_scope).filter(n.project_id == project_id)
    if search and search.strip():
        q = q.filter(n.fqdn.ilike(f"%{escape_like(search.strip().lower())}%", escape="\\"))
    q = apply_name_state(q, state, project_id)
    col = {"fqdn": n.fqdn, "last_seen": n.last_seen, "first_seen": n.first_seen}[sort]
    return q.order_by(col.desc().nullslast() if order == "desc" else col.asc().nullsfirst(), n.id.asc())
