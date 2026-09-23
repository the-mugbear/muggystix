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

import ipaddress
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.db import models
from app.services.host_query_common import escape_like

# Constant, because the derivation admits exactly one reason. It's carried on
# every row so a consumer (an agent especially) can tell *why* a host is
# listed without having to know how the endpoint is implemented.
OUT_OF_SCOPE_REASON = (
    "No subnet mapping — the address falls outside every scope CIDR "
    "defined on this project."
)


def _base_query(db: Session, project_id: int):
    """Hosts with no subnet mapping AND not reachable via an in-scope name.

    v2.322.0 — domain scope adds a third coverage state.  A host that an
    in-scope name resolves to (A/AAAA observation) is neither subnet-in-scope
    (it gets no host_subnet_mappings row; approving a name never approves the
    address's other names or services) nor out of scope — reporting it here
    would flag every host behind an approved load-balancer name as a scope
    violation.  See dns_name_service.host_reachable_via_in_scope_name_condition.
    """
    from app.services.dns_name_service import host_reachable_via_in_scope_name_condition

    mapped = select(models.HostSubnetMapping.id).where(
        models.HostSubnetMapping.host_id == models.Host.id
    )
    return (
        db.query(models.Host)
        .filter(models.Host.project_id == project_id)
        .filter(~mapped.exists())
        .filter(~host_reachable_via_in_scope_name_condition(project_id))
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
        escaped = escape_like(search)
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


# --------------------------------------------------------------------------
# One host: which scope entries cover it (v2.342.0)
# --------------------------------------------------------------------------
COVERAGE_SUBNET = "subnet"
COVERAGE_NAME = "name"
COVERAGE_NONE = "none"


def project_has_any_scope(db: Session, project_id: int) -> bool:
    """Has this project declared any scope at all (a subnet or a domain)?

    Lets a consumer distinguish "no entry covers this host" (worth acting on)
    from "nothing to check against" (the project has no scope yet).
    """
    return bool(
        db.query(
            select(models.Subnet.id)
            .join(models.Scope, models.Scope.id == models.Subnet.scope_id)
            .where(models.Scope.project_id == project_id)
            .exists()
            | select(models.ScopeDomain.id)
            .join(models.Scope, models.Scope.id == models.ScopeDomain.scope_id)
            .where(models.Scope.project_id == project_id)
            .exists()
        ).scalar()
    )


def bulk_scope_coverage(
    db: Session,
    project_id: int,
    host_ids: List[int],
    subnet_mapped_ids: "set[int]",
) -> Dict[int, str]:
    """The three-state coverage for a page of hosts, in one query.

    v2.344.0 — the Hosts list printed "out of scope" for every host without
    a subnet mapping, while the detail card already knew a host reached via
    an approved name is a third state.  The list now carries the same state
    per row.  ``subnet_mapped_ids`` are the hosts the caller already knows
    have a ``host_subnet_mappings`` row (the list endpoint resolves those for
    its subnet column); only the rest are checked for name coverage, with the
    same predicate ``out_of_scope_hosts`` uses, so the two can't disagree.
    """
    from app.services.dns_name_service import host_reachable_via_in_scope_name_condition

    unmapped = [hid for hid in host_ids if hid not in subnet_mapped_ids]
    named: set[int] = set()
    if unmapped:
        named = {
            hid
            for (hid,) in db.query(models.Host.id)
            .filter(
                models.Host.id.in_(unmapped),
                host_reachable_via_in_scope_name_condition(project_id),
            )
            .all()
        }
    out: Dict[int, str] = {}
    for hid in host_ids:
        if hid in subnet_mapped_ids:
            out[hid] = COVERAGE_SUBNET
        elif hid in named:
            out[hid] = COVERAGE_NAME
        else:
            out[hid] = COVERAGE_NONE
    return out


def _prefixlen(cidr: str) -> int:
    try:
        return ipaddress.ip_network(cidr, strict=False).prefixlen
    except ValueError:
        return -1


def host_scope_membership(db: Session, host: models.Host) -> Dict[str, Any]:
    """Every scope entry on the host's project that covers this host.

    The inverse of the list above: instead of "which hosts does no entry
    cover", "which entries cover this host".  Returns::

        {
          "coverage": "subnet" | "name" | "none",
          "project_has_scope": bool,   # any subnet or domain declared at all
          "subnets": [ {id, cidr, description, site, labels: [{id, name, color}]} … ],
          "names":   [ {fqdn, domain, include_subdomains} … ],
        }

    ``subnets`` are the ``host_subnet_mappings`` rows — the same fact scope
    coverage, the /hosts subnet facet and the scope pages read — ordered
    most-specific first, so the first entry is the one the Hosts list shows
    as ``primary_subnet``.  ``names`` are in-scope names that CURRENTLY
    resolve to the address (``current_binding_condition``), each with the
    scope-domain row that admits it.  ``coverage`` follows the three states
    documented on ``_base_query``: a subnet mapping wins; a name alone is
    "reachable via in-scope name", not subnet scope; neither is out of scope.

    ``project_has_scope`` lets the UI distinguish "no entry covers this
    host" (worth acting on) from "this project has declared no scope yet"
    (nothing to check against).
    """
    from app.services.dns_name_service import (
        current_binding_condition,
        scope_domain_covers_condition,
    )

    project_id = host.project_id

    subnet_rows = (
        db.query(models.Subnet)
        .join(models.HostSubnetMapping, models.HostSubnetMapping.subnet_id == models.Subnet.id)
        .join(models.Scope, models.Scope.id == models.Subnet.scope_id)
        .filter(
            models.HostSubnetMapping.host_id == host.id,
            # Belt and braces: mappings written before the project boundary
            # landed in the correlation service may point at another
            # project's subnet until the cleanup migration has run.
            models.Scope.project_id == project_id,
        )
        .all()
    )
    subnet_rows.sort(key=lambda s: (-_prefixlen(s.cidr), s.cidr))

    labels_by_subnet: Dict[int, List[dict]] = {}
    if subnet_rows:
        for subnet_id, label_id, name, color in (
            db.query(
                models.SubnetLabelAssignment.subnet_id,
                models.SubnetLabel.id,
                models.SubnetLabel.name,
                models.SubnetLabel.color,
            )
            .join(models.SubnetLabel, models.SubnetLabel.id == models.SubnetLabelAssignment.label_id)
            .filter(models.SubnetLabelAssignment.subnet_id.in_([s.id for s in subnet_rows]))
            .order_by(models.SubnetLabel.name)
            .all()
        ):
            labels_by_subnet.setdefault(subnet_id, []).append(
                {"id": label_id, "name": name, "color": color}
            )

    subnets = [
        {
            "id": s.id,
            "scope_id": s.scope_id,
            "cidr": s.cidr,
            "description": s.description,
            "site": s.site,
            "labels": labels_by_subnet.get(s.id, []),
        }
        for s in subnet_rows
    ]

    names: List[dict] = []
    if project_id is not None and host.ip_address:
        r = aliased(models.DNSRecord)
        n = models.DNSName
        sd = models.ScopeDomain
        name_rows = (
            db.query(n.fqdn, sd.domain, sd.include_subdomains)
            .select_from(r)
            .join(n, n.id == r.name_id)
            .join(models.Scope, models.Scope.project_id == project_id)
            .join(
                sd,
                and_(sd.scope_id == models.Scope.id, scope_domain_covers_condition(sd, n.fqdn)),
            )
            .filter(
                r.project_id == project_id,
                r.value == host.ip_address,
                current_binding_condition(r),
                n.project_id == project_id,
                n.kind == "fqdn",
            )
            .distinct()
            .order_by(n.fqdn, sd.domain)
            .all()
        )
        names = [
            {"fqdn": fqdn, "domain": domain, "include_subdomains": bool(inc)}
            for fqdn, domain, inc in name_rows
        ]

    if subnets:
        coverage = COVERAGE_SUBNET
    elif names:
        coverage = COVERAGE_NAME
    else:
        coverage = COVERAGE_NONE

    project_has_scope = bool(subnets or names)
    if not project_has_scope and project_id is not None:
        project_has_scope = project_has_any_scope(db, project_id)

    return {
        "coverage": coverage,
        "project_has_scope": bool(project_has_scope),
        "subnets": subnets,
        "names": names,
    }
