"""
Host query construction — extracted from hosts.py in v2.27.0.

The /hosts/ endpoint accepts ~24 filter parameters and a sort spec.
The query construction for that filter set was ~400 lines of pure
SQLAlchemy assembly inline in the route file.  Moving it here keeps
the route handler focused on HTTP concerns (auth, response shaping)
and the query builder testable in isolation.

v2.93.0 — every per-dimension predicate moved to
``host_query_predicates`` so the legacy panel filters below and the
boolean query DSL (``host_query_dsl``) share one implementation.  This
module now wires those helpers into the discrete-parameter signature and
appends the optional ``q=`` DSL filter; the shared primitives
(``escape_like``/``parse_subnets``/``SERVICE_PORT_MAPPINGS``) live in the
leaf module ``host_query_common`` and are re-exported here for callers
that still import them from this module.

The functions are deliberately pure (no FastAPI dependencies beyond
``HTTPException`` for filter-parameter validation) so they can be
exercised against a plain ``Session`` from a contract test.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import cast, func, not_, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import FollowStatus, Annotation as AnnotationModel
from app.db.models_auth import User
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity

from app.services import host_query_predicates as P
# Re-export the dependency-free primitives so existing imports
# (e.g. ``from app.services.host_query import escape_like``) keep working
# unchanged after they were lifted into the leaf module.
from app.services.host_query_common import (  # noqa: F401  (re-exported on purpose)
    SERVICE_PORT_MAPPINGS,
    escape_like,
    parse_subnets,
)


# ---------------------------------------------------------------------------
# Sort-key helper
# ---------------------------------------------------------------------------

def make_correlated_subquery(where_clause):
    """Build a correlated ``COUNT(*)`` scalar subquery against
    ``models.Host`` for use as a sort key.  Used by ``apply_host_sorting``
    so each row can be ordered by a derived count (open ports,
    discoveries, notes, vulnerabilities by severity, …) without
    forcing the listing query to GROUP BY."""
    return (
        select(func.count())
        .where(*where_clause)
        .correlate(models.Host)
        .scalar_subquery()
    )


# ---------------------------------------------------------------------------
# Free-text search — shared by the legacy ``search=`` param and the DSL
# bare-term builder so the power search and the quick-search box behave
# identically.
# ---------------------------------------------------------------------------

def build_search_predicate(db: Session, search: str):
    """Compile a free-text search string into a single ``ColumnElement``.

    Matches IP / hostname / OS across the host, plus port number, service
    name/product (and service→port aliases) via a Port subquery — exactly
    the behaviour the /hosts quick-search box has always had.
    """
    escaped_search = escape_like(search)
    host_search_conditions = [
        models.Host.ip_address.ilike(f'%{escaped_search}%', escape='\\'),
        models.Host.hostname.ilike(f'%{escaped_search}%', escape='\\'),
        models.Host.os_name.ilike(f'%{escaped_search}%', escape='\\'),
        models.Host.os_family.ilike(f'%{escaped_search}%', escape='\\'),
    ]

    search_lower = search.lower().strip()
    port_search_conditions = []

    if search.isdigit():
        port_search_conditions.append(models.Port.port_number == int(search))

    service_ports = SERVICE_PORT_MAPPINGS.get(search_lower)
    if service_ports:
        port_search_conditions.append(models.Port.port_number.in_(service_ports))

    if not search.isdigit():
        port_search_conditions.extend([
            models.Port.service_name.ilike(f'%{escaped_search}%', escape='\\'),
            models.Port.service_product.ilike(f'%{escaped_search}%', escape='\\'),
        ])

    if port_search_conditions:
        search_port_subquery = db.query(models.Host.id).join(models.Port).filter(or_(*port_search_conditions))
        return or_(
            or_(*host_search_conditions),
            models.Host.id.in_(search_port_subquery),
        )
    return or_(*host_search_conditions)


# ---------------------------------------------------------------------------
# The big one — filter assembly for /hosts/ + /hosts/filters/data
# ---------------------------------------------------------------------------

def build_filtered_host_query(
    db: Session,
    current_user: User,
    state: Optional[str] = None,
    search: Optional[str] = None,
    ports: Optional[str] = None,
    services: Optional[str] = None,
    port_states: Optional[str] = None,
    has_open_ports: Optional[bool] = None,
    os_filter: Optional[str] = None,
    subnets: Optional[str] = None,
    has_critical_vulns: Optional[bool] = None,
    has_high_vulns: Optional[bool] = None,
    has_medium_vulns: Optional[bool] = None,
    has_low_vulns: Optional[bool] = None,
    has_exploit_available: Optional[bool] = None,
    has_test_execution: Optional[bool] = None,
    follow_status: Optional[str] = None,
    out_of_scope_only: Optional[bool] = None,
    scan_ids: Optional[str] = None,
    first_seen_in_scan: Optional[bool] = None,
    with_notes_only: Optional[bool] = None,
    has_web_interface: Optional[bool] = None,
    tech: Optional[str] = None,
    tags: Optional[str] = None,
    subnet_labels: Optional[str] = None,
    assigned_to: Optional[str] = None,
    project_id: int = None,
    q: Optional[str] = None,
):
    """Build a filtered ``Host`` query (no eager loading).

    Reused by both the listing endpoint and the filter-metadata
    endpoint so they apply the same predicates and stay in sync.  Every
    block delegates to ``host_query_predicates`` (the single source of
    truth shared with the DSL); ``q`` appends the boolean DSL filter,
    ANDed with the discrete params.
    """
    query = db.query(models.Host)

    if project_id is not None:
        query = query.filter(models.Host.project_id == project_id)

    if state:
        query = query.filter(P.state_predicate([state]))

    if os_filter:
        query = query.filter(P.os_predicate([os_filter]))

    if subnets:
        subnet_pred = P.subnet_predicate([subnets])
        if subnet_pred is not None:
            query = query.filter(subnet_pred)

    # Port dimensions are fused into one Port subquery (a single port row
    # must satisfy all of ports/services/port_states/require_open) — see
    # ``port_match_subquery``.  ``has_open_ports=False`` is a standalone
    # exclusion of open-port hosts and intentionally ignores the other
    # port filters, preserving the long-standing behaviour.
    if ports or services or port_states or has_open_ports:
        port_ints = [int(p.strip()) for p in ports.split(',') if p.strip().isdigit()] if ports else None
        service_list = [s.strip() for s in services.split(',') if s.strip()] if services else None
        state_list = [s.strip().lower() for s in port_states.split(',') if s.strip()] if port_states else None
        if has_open_ports is False:
            query = query.filter(not_(models.Host.id.in_(P.port_match_subquery(db, require_open=True))))
        else:
            query = query.filter(
                models.Host.id.in_(
                    P.port_match_subquery(
                        db,
                        ports=port_ints,
                        services=service_list,
                        port_states=state_list,
                        require_open=bool(has_open_ports),
                    )
                )
            )

    if search:
        query = query.filter(build_search_predicate(db, search))

    if with_notes_only:
        query = query.filter(P.has_notes_predicate(db))

    if follow_status:
        if follow_status not in ("none", "in_review_any") and follow_status not in {s.value for s in FollowStatus}:
            raise HTTPException(status_code=400, detail="Invalid follow status filter")
        query = query.filter(P.follow_predicate(db, follow_status, current_user))

    if out_of_scope_only:
        query = query.outerjoin(
            models.HostSubnetMapping,
            models.HostSubnetMapping.host_id == models.Host.id,
        ).filter(models.HostSubnetMapping.host_id.is_(None))

    severities = []
    if has_critical_vulns:
        severities.append('CRITICAL')
    if has_high_vulns:
        severities.append('HIGH')
    if has_medium_vulns:
        severities.append('MEDIUM')
    if has_low_vulns:
        severities.append('LOW')
    if severities:
        query = query.filter(P.severity_predicate(db, severities))

    if has_exploit_available:
        query = query.filter(P.has_exploit_predicate(db))

    if has_test_execution:
        query = query.filter(P.has_test_execution_predicate(db))

    if scan_ids:
        try:
            scan_id_list = [int(s.strip()) for s in scan_ids.split(',') if s.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scan_ids parameter")
        if scan_id_list:
            query = query.filter(P.scan_predicate(db, scan_id_list, first_seen_only=bool(first_seen_in_scan)))

    # v2.12.1: web interface filters.  ``has_web_interface`` narrows to
    # hosts with at least one web_interfaces row (or, when false, those
    # without).  The predicate filters ``host_id IS NOT NULL`` so the
    # ``false`` branch is correct (the old inline code's ``NOT IN`` over a
    # nullable column returned zero rows — a latent bug, fixed here).
    if has_web_interface is not None:
        wi_pred = P.has_web_interface_predicate(db)
        query = query.filter(wi_pred if has_web_interface else not_(wi_pred))

    if tech:
        tech_list = [t.strip() for t in tech.split(',') if t.strip()]
        if tech_list:
            query = query.filter(P.tech_predicate(db, tech_list))

    # v2.71.0 — tag filter.  Comma-separated tag IDs; OR semantics.
    if tags:
        tag_id_list = [int(t.strip()) for t in tags.split(',') if t.strip().isdigit()]
        if tag_id_list:
            query = query.filter(P.tag_predicate_by_id(db, tag_id_list))

    # v2.86.0 — subnet-label filter.  Comma-separated label IDs; OR
    # semantics within the group, project-scoped join chain.
    if subnet_labels and project_id is not None:
        label_id_list = [int(t.strip()) for t in subnet_labels.split(',') if t.strip().isdigit()]
        if label_id_list:
            query = query.filter(P.label_predicate_by_id(db, label_id_list, project_id))

    # v2.71.0 — assignment filter.  "me" / "any" / numeric user id.
    if assigned_to:
        assigned_pred = P.assigned_predicate(db, assigned_to, current_user)
        if assigned_pred is not None:
            query = query.filter(assigned_pred)

    # v2.93.0 — boolean query DSL.  Appended last; ANDs with every
    # discrete param above.  A malformed ``q`` raises ``DSLError`` →
    # HTTP 400 before any rows are fetched.  Imported lazily to keep the
    # module-load graph acyclic (the DSL imports this module's
    # ``build_search_predicate``).
    if q:
        from app.services.host_query_dsl import BuildCtx, evaluate, parse_query
        query = query.filter(evaluate(parse_query(q), BuildCtx(db, current_user, project_id)))

    return query


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def apply_host_sorting(query, sort_by: str, sort_order: str):
    """Apply a primary sort + standard tiebreakers to a filtered host query.

    Lazy-constructs the correlated count subqueries — a sort by
    ``ip_address`` doesn't need to compute vuln counts; a sort by
    ``critical_vulns`` doesn't pay for note counts.  The two
    high-value tiebreakers (high-vuln count, open-port count) are
    always appended unless they're already the primary key.
    """
    sort_desc = sort_order == "desc"

    _subquery_cache: dict[str, object] = {}

    def _open_ports():
        if "open_ports" not in _subquery_cache:
            _subquery_cache["open_ports"] = make_correlated_subquery([
                models.Port.host_id == models.Host.id,
                models.Port.state == 'open',
            ])
        return _subquery_cache["open_ports"]

    def _note_count():
        if "note_count" not in _subquery_cache:
            _subquery_cache["note_count"] = make_correlated_subquery([
                AnnotationModel.host_id == models.Host.id,
            ])
        return _subquery_cache["note_count"]

    def _discovery_count():
        if "discovery_count" not in _subquery_cache:
            _subquery_cache["discovery_count"] = make_correlated_subquery([
                models.HostScanHistory.host_id == models.Host.id,
            ])
        return _subquery_cache["discovery_count"]

    def _critical_vulns():
        if "critical_vulns" not in _subquery_cache:
            _subquery_cache["critical_vulns"] = make_correlated_subquery([
                Vulnerability.host_id == models.Host.id,
                Vulnerability.severity == VulnerabilitySeverity.CRITICAL,
            ])
        return _subquery_cache["critical_vulns"]

    def _high_vulns():
        if "high_vulns" not in _subquery_cache:
            _subquery_cache["high_vulns"] = make_correlated_subquery([
                Vulnerability.host_id == models.Host.id,
                Vulnerability.severity == VulnerabilitySeverity.HIGH,
            ])
        return _subquery_cache["high_vulns"]

    # Sort IPs by numeric/octet order, not lexicographically (string order puts
    # 10.0.0.10 before 10.0.0.2 and 10.x before 9.x). Postgres' inet type orders
    # correctly (and handles IPv6); SQLite (tests) has no inet, so it falls back
    # to the string column — acceptable for the small test fixtures.
    bind = query.session.bind if query.session is not None else None
    _is_postgres = bind is not None and bind.dialect.name == "postgresql"
    ip_sort_key = cast(models.Host.ip_address, INET) if _is_postgres else models.Host.ip_address

    sortable_fields = {
        "ip_address": lambda: ip_sort_key,
        "hostname": lambda: func.coalesce(models.Host.hostname, models.Host.ip_address),
        "open_ports": _open_ports,
        "note_count": _note_count,
        "discovery_count": _discovery_count,
        "critical_vulns": _critical_vulns,
        "high_vulns": _high_vulns,
        "last_seen": lambda: func.coalesce(models.Host.last_seen, models.Host.first_seen),
    }

    primary_factory = sortable_fields.get(sort_by, _critical_vulns)
    primary_sort = primary_factory()
    primary_order = primary_sort.desc() if sort_desc else primary_sort.asc()

    tiebreakers = []
    if sort_by != "high_vulns":
        tiebreakers.append(_high_vulns().desc())
    if sort_by != "open_ports":
        tiebreakers.append(_open_ports().desc())
    tiebreakers.append(ip_sort_key.asc())

    return query.order_by(primary_order, *tiebreakers)
