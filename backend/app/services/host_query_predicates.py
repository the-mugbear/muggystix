"""Single source of truth for /hosts filter predicates.

Every filter dimension on the Hosts page — port, OS, subnet, tag, the
``has:*`` family, the evidence-search fields, … — is expressed here as a
pure function returning a SQLAlchemy ``ColumnElement``.  Both callers use
these helpers:

* the legacy discrete-parameter path in ``host_query.build_filtered_host_query``
  (``state=``, ``ports=``, ``tags=`` …), and
* the boolean query DSL (``host_query_dsl`` field builders).

Keeping the predicate logic in one place means a change to, say, how a
tag filter resolves is made once and both doors inherit it — no drift
between the panel and the ``q=`` power search.

The functions are deliberately pure (they build expressions, they don't
mutate a query) and take a value *list* wherever the dimension is
naturally multi-valued, OR-ing within the list.  That lets the DSL hand a
single value while the legacy path hands the comma-split list, with
identical semantics.

Behaviour parity with the pre-extraction inline blocks is contractual and
covered by ``tests/test_scan_hosts_filter.py`` — the emitted SQL must be
the same so the query plan is unchanged.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from sqlalchemy import cast, func, or_, false
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import exists
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import String as SAString

from app.db import models
from app.db.models import FollowStatus, HostFollow, HostNote as HostNoteModel
from app.db.models_auth import User
from app.db.models_agent import TestExecutionResult, TestPlanEntry
from app.db.models_risk import HostRiskAssessment
from app.db.models_vulnerability import Vulnerability

# Leaf module — no import cycle (host_query imports *us*, not the reverse).
from app.services.host_query_common import (  # noqa: F401  (re-exported on purpose)
    SERVICE_PORT_MAPPINGS,
    escape_like,
    parse_subnets,
)


# ---------------------------------------------------------------------------
# Simple Host-column predicates
# ---------------------------------------------------------------------------

def state_predicate(values: Sequence[str]) -> ColumnElement:
    """``Host.state`` matches any of ``values``.

    The legacy path passes a single state; ``in_`` over a one-element list
    is equivalent to the old ``== state`` while giving the DSL OR-within-
    field for free (``state:up,down``).
    """
    return models.Host.state.in_(list(values))


def ip_predicate(values: Sequence[str]) -> ColumnElement:
    """``Host.ip_address`` ILIKE-matches any of ``values`` (substring)."""
    return or_(*[
        models.Host.ip_address.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ])


def hostname_predicate(values: Sequence[str]) -> ColumnElement:
    """``Host.hostname`` ILIKE-matches any of ``values`` (substring)."""
    return or_(*[
        models.Host.hostname.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ])


def os_predicate(values: Sequence[str]) -> ColumnElement:
    """OS name OR family ILIKE-matches any of ``values``.

    Mirrors the legacy ``os_filter`` block: each value matches against
    both ``os_name`` and ``os_family``; multiple values union.
    """
    conditions = []
    for v in values:
        escaped = escape_like(v)
        conditions.append(models.Host.os_name.ilike(f'%{escaped}%', escape='\\'))
        conditions.append(models.Host.os_family.ilike(f'%{escaped}%', escape='\\'))
    return or_(*conditions)


def subnet_predicate(values: Sequence[str]) -> Optional[ColumnElement]:
    """Host falls within any of the given CIDRs / IP fragments.

    Delegates to :func:`parse_subnets` (``inet <<=`` containment per CIDR,
    prefix-match fallback for non-CIDR fragments).  Returns ``None`` when
    nothing usable was supplied so callers can skip the filter, matching
    the legacy guard.
    """
    conditions = parse_subnets(",".join(values))
    if not conditions:
        return None
    return or_(*conditions)


# ---------------------------------------------------------------------------
# Port-dimension predicates
# ---------------------------------------------------------------------------
#
# The legacy path fuses ports + services + port_states + has_open_ports
# into ONE subquery joined to Port, so a single port row must satisfy all
# of them ("has a port that is 80 AND open").  ``port_match_subquery`` is
# that single builder; the legacy block calls it once with every supplied
# dimension, while each DSL leaf (``port:``, ``service:``, ``portstate:``)
# calls it with just its own dimension and composes via the boolean
# evaluator.

def port_match_subquery(
    db: Session,
    *,
    ports: Optional[Sequence[int]] = None,
    services: Optional[Sequence[str]] = None,
    port_states: Optional[Sequence[str]] = None,
    require_open: bool = False,
):
    """Return a ``db.query(Host.id).join(Port)`` narrowed by the supplied
    port dimensions (all applied to the *same* Port row)."""
    sub = db.query(models.Host.id).join(models.Port)
    if ports:
        sub = sub.filter(models.Port.port_number.in_(list(ports)))
    if services:
        sub = sub.filter(or_(*[
            models.Port.service_name.ilike(f'%{escape_like(s)}%', escape='\\')
            for s in services
        ]))
    if port_states:
        sub = sub.filter(models.Port.state.in_([s.lower() for s in port_states]))
    if require_open:
        sub = sub.filter(models.Port.state == 'open')
    return sub


def port_predicate(db: Session, values: Sequence) -> ColumnElement:
    """Host has at least one port whose number is in ``values``.

    RV-5 — an empty port list must NOT broaden to "any port" (the legacy
    ``port_match_subquery`` skips an empty ``ports`` filter).  The DSL
    builder validates and rejects non-numeric input upstream; this guard
    is defense-in-depth for any other caller.
    """
    port_ints = [int(v) for v in values if str(v).strip().isdigit()]
    if not port_ints:
        return false()
    return models.Host.id.in_(port_match_subquery(db, ports=port_ints))


def service_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has at least one port whose service name ILIKE-matches a value."""
    return models.Host.id.in_(port_match_subquery(db, services=list(values)))


def portstate_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has at least one port in any of the given states."""
    return models.Host.id.in_(port_match_subquery(db, port_states=list(values)))


def has_open_ports_predicate(db: Session) -> ColumnElement:
    """Host has at least one ``open`` port."""
    return models.Host.id.in_(port_match_subquery(db, require_open=True))


# ---------------------------------------------------------------------------
# Web-interface / technology predicates
# ---------------------------------------------------------------------------

def tech_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose ``technologies`` JSON contains any
    of ``values`` (cast-to-text substring, dialect-portable)."""
    conditions = [
        cast(models.WebInterface.technologies, SAString).ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = (
        db.query(models.WebInterface.host_id)
        .filter(models.WebInterface.host_id.isnot(None), or_(*conditions))
        .distinct()
    )
    return models.Host.id.in_(sub)


def has_web_interface_predicate(db: Session) -> ColumnElement:
    """Host has at least one web interface row."""
    sub = (
        db.query(models.WebInterface.host_id)
        .filter(models.WebInterface.host_id.isnot(None))
        .distinct()
    )
    return models.Host.id.in_(sub)


def _web_text_predicate(db: Session, column, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose ``column`` ILIKE-matches any value.

    ``host_id`` is nullable on ``web_interfaces`` so the subquery filters
    it NOT NULL — otherwise ``NOT header:x`` would wrongly drop hosts via
    the SQL NULL-in-NOT-IN footgun."""
    conditions = [column.ilike(f'%{escape_like(v)}%', escape='\\') for v in values]
    sub = (
        db.query(models.WebInterface.host_id)
        .filter(models.WebInterface.host_id.isnot(None), or_(*conditions))
        .distinct()
    )
    return models.Host.id.in_(sub)


def header_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose ``server_header`` matches any value."""
    return _web_text_predicate(db, models.WebInterface.server_header, values)


def webtitle_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose page ``title`` matches any value."""
    return _web_text_predicate(db, models.WebInterface.title, values)


# ---------------------------------------------------------------------------
# Vulnerability / evidence predicates
# ---------------------------------------------------------------------------

def cve_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a vulnerability whose ``cve_id`` ILIKE-matches any value."""
    conditions = [
        Vulnerability.cve_id.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = db.query(Vulnerability.host_id).filter(or_(*conditions)).distinct()
    return models.Host.id.in_(sub)


def vuln_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a vulnerability whose ``title`` ILIKE-matches any value."""
    conditions = [
        Vulnerability.title.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = db.query(Vulnerability.host_id).filter(or_(*conditions)).distinct()
    return models.Host.id.in_(sub)


def severity_predicate(db: Session, severities: Iterable[str]) -> ColumnElement:
    """Host has a vulnerability of any of the given severities (upper-case
    ``CRITICAL``/``HIGH``/``MEDIUM``/``LOW``)."""
    sev_list = [s.upper() for s in severities]
    sub = db.query(Vulnerability.host_id).filter(Vulnerability.severity.in_(sev_list)).distinct()
    return models.Host.id.in_(sub)


def has_exploit_predicate(db: Session) -> ColumnElement:
    """Host has a vulnerability flagged exploitable by Nessus."""
    sub = db.query(Vulnerability.host_id).filter(Vulnerability.exploitable.is_(True)).distinct()
    return models.Host.id.in_(sub)


def risk_predicate(db: Session, min_score: int) -> ColumnElement:
    """Host has a risk assessment at or above ``min_score``."""
    sub = db.query(HostRiskAssessment.host_id).filter(
        HostRiskAssessment.risk_score >= min_score
    )
    return models.Host.id.in_(sub)


# ---------------------------------------------------------------------------
# Notes / tested predicates
# ---------------------------------------------------------------------------

def has_notes_predicate(db: Session) -> ColumnElement:
    """Host has at least one note."""
    sub = db.query(HostNoteModel.host_id).distinct()
    return models.Host.id.in_(sub)


def note_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a note whose ``body`` ILIKE-matches any value."""
    conditions = [
        HostNoteModel.body.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = db.query(HostNoteModel.host_id).filter(or_(*conditions)).distinct()
    return models.Host.id.in_(sub)


def has_test_execution_predicate(db: Session) -> ColumnElement:
    """Host has had at least one agentic test executed against it."""
    sub = (
        db.query(TestPlanEntry.host_id)
        .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
        .distinct()
    )
    return models.Host.id.in_(sub)


# ---------------------------------------------------------------------------
# Tag / label predicates (by id for the panel, by name for the DSL)
# ---------------------------------------------------------------------------

def tag_predicate_by_id(db: Session, tag_ids: Sequence[int]) -> ColumnElement:
    """Host carries any of the given tag IDs (OR)."""
    sub = (
        db.query(models.HostTagAssignment.host_id)
        .filter(models.HostTagAssignment.tag_id.in_(list(tag_ids)))
        .distinct()
    )
    return models.Host.id.in_(sub)


def tag_predicate_by_name(db: Session, names: Sequence[str], project_id: int) -> ColumnElement:
    """Host carries any tag whose (case-insensitive) name matches, scoped
    to ``project_id``.

    The DSL resolves tags by name (ids are meaningless in a shared
    ``?q=``).  Name→host resolution is the attack surface, so the join is
    explicitly constrained by ``HostTag.project_id`` — defense in depth
    alongside the outer ``Host.project_id`` filter.
    """
    lowered = [n.lower() for n in names]
    sub = (
        db.query(models.HostTagAssignment.host_id)
        .join(models.HostTag, models.HostTag.id == models.HostTagAssignment.tag_id)
        .filter(
            models.HostTag.project_id == project_id,
            func.lower(models.HostTag.name).in_(lowered),
        )
        .distinct()
    )
    return models.Host.id.in_(sub)


def label_predicate_by_id(db: Session, label_ids: Sequence[int], project_id: int) -> ColumnElement:
    """Host sits in a subnet carrying any of the given label IDs, scoped
    to ``project_id``."""
    sub = (
        db.query(models.HostSubnetMapping.host_id)
        .join(
            models.SubnetLabelAssignment,
            models.SubnetLabelAssignment.subnet_id == models.HostSubnetMapping.subnet_id,
        )
        .join(
            models.SubnetLabel,
            models.SubnetLabel.id == models.SubnetLabelAssignment.label_id,
        )
        .filter(
            models.SubnetLabelAssignment.label_id.in_(list(label_ids)),
            models.SubnetLabel.project_id == project_id,
        )
        .distinct()
    )
    return models.Host.id.in_(sub)


def label_predicate_by_name(db: Session, names: Sequence[str], project_id: int) -> ColumnElement:
    """Host sits in a subnet carrying any label whose (case-insensitive)
    name matches, scoped to ``project_id``."""
    lowered = [n.lower() for n in names]
    sub = (
        db.query(models.HostSubnetMapping.host_id)
        .join(
            models.SubnetLabelAssignment,
            models.SubnetLabelAssignment.subnet_id == models.HostSubnetMapping.subnet_id,
        )
        .join(
            models.SubnetLabel,
            models.SubnetLabel.id == models.SubnetLabelAssignment.label_id,
        )
        .filter(
            models.SubnetLabel.project_id == project_id,
            func.lower(models.SubnetLabel.name).in_(lowered),
        )
        .distinct()
    )
    return models.Host.id.in_(sub)


# ---------------------------------------------------------------------------
# Follow / assignment / scan predicates
# ---------------------------------------------------------------------------

def follow_predicate(db: Session, status: str, current_user: User) -> ColumnElement:
    """Review-status predicate.

    ``none`` → no follow row for the caller; ``in_review_any`` → any user
    marked In Review; otherwise → the caller's follow row with that
    status.  Mirrors the legacy ``follow_status`` block including the
    valid-status guard (raises via the caller).
    """
    if status == "none":
        followed = db.query(HostFollow.host_id).filter(HostFollow.user_id == current_user.id)
        return models.Host.id.notin_(followed)
    if status == "in_review_any":
        in_review = db.query(HostFollow.host_id).filter(
            HostFollow.status == FollowStatus.IN_REVIEW.value
        )
        return models.Host.id.in_(in_review)
    follow_ids = db.query(HostFollow.host_id).filter(
        HostFollow.user_id == current_user.id, HostFollow.status == status
    )
    return models.Host.id.in_(follow_ids)


def assigned_predicate(db: Session, value: str, current_user: User) -> Optional[ColumnElement]:
    """Assignment predicate: ``any`` → assigned to anyone, ``me`` → the
    caller, or a numeric user id.  Returns ``None`` for an unusable value
    so callers skip the filter (legacy parity)."""
    if value == "any":
        assigned = db.query(HostFollow.host_id).filter(HostFollow.assigned_at.isnot(None))
        return models.Host.id.in_(assigned)
    assignee_id = current_user.id if value == "me" else (int(value) if value.isdigit() else None)
    if assignee_id is None:
        return None
    assigned = db.query(HostFollow.host_id).filter(
        HostFollow.user_id == assignee_id, HostFollow.assigned_at.isnot(None)
    )
    return models.Host.id.in_(assigned)


def scan_predicate(db: Session, scan_ids: Sequence[int], first_seen_only: bool = False) -> ColumnElement:
    """Host appears in any of the given scans; with ``first_seen_only`` the
    host must have been *first* discovered in one of them."""
    history_query = db.query(models.HostScanHistory.host_id).filter(
        models.HostScanHistory.scan_id.in_(list(scan_ids))
    )
    if first_seen_only:
        earlier = aliased(models.HostScanHistory)
        earlier_exists = exists().where(
            (earlier.host_id == models.HostScanHistory.host_id)
            & (earlier.discovered_at < models.HostScanHistory.discovered_at)
        )
        history_query = history_query.filter(~earlier_exists)
    return models.Host.id.in_(history_query)
