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

from typing import Iterable, List, Optional, Sequence

from sqlalchemy import and_, cast, func, literal_column, or_, false
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import exists
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import String as SAString

from app.db import models
from app.db.models import FollowStatus, HostFollow, Annotation as AnnotationModel
from app.db.models_auth import User
from app.services import smb_signing as smb_signing_states
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.services.host_test_queries import planned_host_ids, tested_host_ids
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_confidence import NetexecResult

# Leaf module — no import cycle (host_query imports *us*, not the reverse).
from app.services.host_query_common import (  # noqa: F401  (re-exported on purpose)
    SERVICE_PORT_MAPPINGS,
    escape_like,
    parse_subnets,
)
# Shared condition logic — the SAME id-sets the systemic-insights view counts,
# so the `has:eol`/`has:weak_auth`/`has:cert_issue` drill-downs land on exactly
# the hosts behind a systemic blind-spot's headline number.  (Importing this
# does not create a cycle: host_condition_sets -> subnet_insight_service, and
# subnet_insight_service imports neither host_query nor this module.)
from app.services.host_condition_sets import (
    CLEARTEXT_PORTS,
    cert_issue_host_ids,
    eol_os_host_ids,
    weak_auth_host_ids,
    weak_tls_host_ids,
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


#: The three scope-coverage states (v2.322.0; the list row's `scope_coverage`).
SCOPE_COVERAGE_STATES = ("subnet", "name", "none")


def scope_coverage_predicate(values: Sequence[str], project_id: int) -> ColumnElement:
    """Host in any of the given coverage states (v2.424.0): ``subnet`` — in a
    scope subnet; ``name`` — no subnet, but an in-scope name currently
    resolves to it; ``none`` — neither (``out_of_scope_count``'s rule).
    The three partition the project's hosts, so Operations' coverage line
    adds up and each count opens its own list."""
    from app.services.dns_name_service import host_reachable_via_in_scope_name_condition

    mapped = exists().where(models.HostSubnetMapping.host_id == models.Host.id)
    by_name = host_reachable_via_in_scope_name_condition(project_id)
    conditions = []
    for value in {v.strip().lower() for v in values}:
        if value == "subnet":
            conditions.append(mapped)
        elif value == "name":
            conditions.append(and_(~mapped, by_name))
        elif value == "none":
            conditions.append(and_(~mapped, ~by_name))
    return or_(*conditions) if conditions else false()


#: ``vulnscan:`` values → the state ``evidence_service`` defines (review
#: 2026-10-01).  The Evidence page's three counts each open one of these.
VULN_SCAN_CREDENTIALED_VALUES = {
    "credentialed": "yes",
    "uncredentialed": "no",
    "unstated": "not_stated",
}


def vuln_scan_credentialed_predicate(values: Sequence[str]) -> ColumnElement:
    """Host assessed for vulnerabilities whose scans say they authenticated
    (``credentialed``), say they did not (``uncredentialed``), or say neither
    (``unstated``).  Correlated EXISTS on the host; the rule itself is
    ``evidence_service.vuln_scan_credentialed_condition`` — never a second
    copy here.  A host that is not assessed matches none of the three."""
    from app.services.evidence_service import vuln_scan_credentialed_condition

    states = {
        VULN_SCAN_CREDENTIALED_VALUES[v.strip().lower()]
        for v in values if v.strip().lower() in VULN_SCAN_CREDENTIALED_VALUES
    }
    conditions = [vuln_scan_credentialed_condition(s) for s in sorted(states)]
    return or_(*conditions) if conditions else false()


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
#
# v2.403.0 — OPEN BY DEFAULT.  A port / service / product / version match
# requires the port to be ``open`` unless the same condition names a state.
# For a closed or filtered port nmap fills ``service_name`` from its
# port-number table, so "ssh 22/tcp · closed" is no evidence SSH runs: the
# Hosts page matched 24 hosts for "service ssh", 20 of them closed/filtered
# only.  The explicit forms are the structured ``port_states`` list (``any``
# lifts the restriction) and the DSL's ``@state`` suffix (``service:ssh@closed``,
# ``port:22@any``).  ``resolve_endpoint_states`` is the one rule; the
# tool-ready export's per-host port narrowing (hosts.py) and the frontend's
# ``utils/endpointMatch.ts`` mirror it.

#: The explicit "no state restriction" value (structured ``port_states`` and
#: the DSL ``@any`` suffix).
PORT_STATE_ANY = "any"
#: States a condition may name explicitly — nmap's six plus ``any``.
EXPLICIT_PORT_STATES = (
    "open", "closed", "filtered", "unfiltered", "open|filtered", "closed|filtered", PORT_STATE_ANY,
)


def resolve_endpoint_states(
    port_states: Optional[Sequence[str]], *, has_endpoint: bool,
) -> Optional[List[str]]:
    """The port states a match accepts, or ``None`` for no state restriction.

    * explicit states win; ``any`` among them lifts the restriction;
    * none named + a port/service/product/version condition → ``["open"]``;
    * none named and no such condition (a bare state filter) → ``None``.
    """
    states = [s.strip().lower() for s in (port_states or []) if s and s.strip()]
    if states:
        return None if PORT_STATE_ANY in states else states
    return ["open"] if has_endpoint else None


def port_match_subquery(
    db: Session,
    *,
    ports: Optional[Sequence[int]] = None,
    services: Optional[Sequence[str]] = None,
    port_states: Optional[Sequence[str]] = None,
    require_open: bool = False,
    project_id: Optional[int] = None,
):
    """Return a ``db.query(Host.id).join(Port)`` narrowed by the supplied
    port dimensions (all applied to the *same* Port row).  A port or
    service condition matches OPEN ports unless ``port_states`` names a
    state (``resolve_endpoint_states``).

    The host predicates below no longer use this (they are correlated
    ``EXISTS`` — ``host_has_port``); it remains for callers that want the id
    list itself.  ``project_id`` (review 2026-10-01 R19) confines it to one
    project's hosts: it never changes which of a project's hosts match, only
    what Postgres reads — without it the subquery is every project's ports.
    Pass it wherever the project is known.
    """
    sub = db.query(models.Host.id).join(models.Port)
    if project_id is not None:
        sub = sub.filter(models.Host.project_id == project_id)
    return sub.filter(*port_match_conditions(ports, services, port_states, require_open))


def port_match_conditions(
    ports: Optional[Sequence[int]] = None,
    services: Optional[Sequence[str]] = None,
    port_states: Optional[Sequence[str]] = None,
    require_open: bool = False,
) -> List[ColumnElement]:
    """The conditions ONE Port row must meet — shared by
    ``port_match_subquery`` and ``host_has_port``."""
    conditions: List[ColumnElement] = []
    if ports:
        conditions.append(models.Port.port_number.in_(list(ports)))
    if services:
        conditions.append(or_(*[
            models.Port.service_name.ilike(f'%{escape_like(s)}%', escape='\\')
            for s in services
        ]))
    states = resolve_endpoint_states(port_states, has_endpoint=bool(ports or services))
    if require_open and states in (None, ["open"]):
        states = ["open"]
    elif require_open:
        # An explicit other state beside "must be open": both hold (the
        # long-standing contradiction stays a contradiction).
        conditions.append(models.Port.state == 'open')
    if states:
        conditions.append(port_state_condition(states))
    return conditions


def host_has_port(*conditions: ColumnElement) -> ColumnElement:
    """The host of the enclosing query has a Port row meeting ``conditions``
    — a correlated ``EXISTS`` (review 2026-10-01 R19/R21).

    The port predicates used to be ``Host.id IN (SELECT … FROM hosts JOIN
    ports …)`` over every project's ports.  That plans well only un-negated:
    under ``NOT`` Postgres cannot make an anti-join of ``NOT IN`` and falls
    back to a list it rescans per host once it outgrows work_mem — the
    "no open ports" filter did not finish in 60 s at 120k hosts (measured).
    ``EXISTS`` correlated on the host is a semi-join, ``NOT EXISTS`` an
    anti-join, and either way it is reached through the outer query's hosts,
    so it is confined to the project without being told which one.  Same
    hosts: ``ports_v2.host_id`` is NOT NULL and references the host.
    """
    return (
        exists()
        .where(models.Port.host_id == models.Host.id, *conditions)
        .correlate(models.Host)
    )


def port_state_condition(states: Sequence[str]) -> ColumnElement:
    """``Port.state`` in ``states`` — a plain equality for one state."""
    if len(states) == 1:
        return models.Port.state == states[0]
    return models.Port.state.in_(list(states))


def port_predicate(db: Session, values: Sequence, states: Optional[Sequence[str]] = None) -> ColumnElement:
    """Host has at least one OPEN port whose number is in ``values`` — or
    one in ``states`` when given (``["any"]`` = any state).

    RV-5 — an empty port list must NOT broaden to "any port" (the legacy
    ``port_match_subquery`` skips an empty ``ports`` filter).  The DSL
    builder validates and rejects non-numeric input upstream; this guard
    is defense-in-depth for any other caller.
    """
    port_ints = [int(v) for v in values if str(v).strip().isdigit()]
    if not port_ints:
        return false()
    return host_has_port(*port_match_conditions(ports=port_ints, port_states=states))


def service_predicate(db: Session, values: Sequence[str], states: Optional[Sequence[str]] = None) -> ColumnElement:
    """Host has at least one OPEN port (or one in ``states``) whose service
    name ILIKE-matches a value."""
    return host_has_port(*port_match_conditions(services=list(values), port_states=states))


def product_version_text() -> ColumnElement:
    """A port's product and version as one string, "OpenSSH 7.4".

    Built with ``||`` rather than ``concat()`` (review 2026-10-01 R19):
    ``concat()`` is only STABLE in Postgres and cannot be indexed, while this
    expression is what ``ix_trgm_port_product_version`` (revision
    ``c3f6b9d2e5a7``) is built on — keep the two in step.  With both sides
    coalesced the result is the same string ``concat`` gave.
    """
    return (
        func.coalesce(models.Port.service_product, literal_column("''"))
        .concat(literal_column("' '"))
        .concat(func.coalesce(models.Port.service_version, literal_column("''")))
    )


def version_predicate(db: Session, values: Sequence[str], states: Optional[Sequence[str]] = None) -> ColumnElement:
    """Host has an open port (or one in ``states``) whose service product or
    version (or the two together, "OpenSSH 7.4") ILIKE-matches a value
    (v2.390.0 — `service:` matched the name only, so "which hosts run
    OpenSSH 7.x" had no filter)."""
    joined = product_version_text()
    # LIKE wildcards in the value are literal, as in service: (review
    # 2026-09-23 R12 — `path:/admin_x` matched `/adminYx`).
    #
    # ONE arm (R19): a value found in the product or in the version is found
    # in "product version" too, so the two column arms the predicate used to
    # OR in matched nothing extra — and an OR whose arms need three different
    # indexes is an OR Postgres scans the table for.
    # ``tests/test_read_path_review.py`` pins old == new.
    conds = [joined.ilike(f"%{escape_like(v)}%", escape="\\") for v in values if v]
    if not conds:
        return false()
    resolved = resolve_endpoint_states(states, has_endpoint=True)
    return host_has_port(*([port_state_condition(resolved)] if resolved else []), or_(*conds))


def webpath_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a path content discovery found that ILIKE-matches a value
    (v2.390.0 — the paths were an unqueryable string before)."""
    conds = [models.WebPath.path.ilike(f"%{escape_like(v)}%", escape="\\") for v in values if v]
    if not conds:
        return false()
    return _host_has(models.WebPath.host_id, or_(*conds))


def issue_predicate(db: Session, values: Sequence[str], project_id: int) -> ColumnElement:
    """Host carries a scanner observation of exactly this issue
    (``Vulnerability.issue_key`` — the key the Findings page's scanner
    observations group by).  Exact, not a substring: it is the "all N hosts"
    link of an issue whose host list is too long to show there."""
    keys = [v for v in values if v]
    if not keys:
        return false()
    _H = aliased(models.Host)
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, Vulnerability.issue_key.in_(keys))
    )
    return models.Host.id.in_(sub)


def portstate_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has at least one port in any of the given states."""
    return host_has_port(*port_match_conditions(port_states=list(values)))


def _host_has(host_id_column, *conditions: ColumnElement) -> ColumnElement:
    """The host of the enclosing query has a row in ``host_id_column``'s table
    meeting ``conditions`` — the correlated ``EXISTS`` of ``host_has_port``,
    for the other child tables (review 2026-10-07).

    These were ``Host.id IN (SELECT host_id FROM <child> WHERE …)`` with the
    subquery over EVERY project's rows.  At the top level Postgres turns that
    into a semi-join; under ``OR`` or ``NOT`` (``NOT path:admin``,
    ``follow:none``) it ran the subquery whole.  Correlated on the host, it is
    reached through the outer query's hosts either way.  Same hosts as before:
    a NULL ``host_id`` never equals a host's id."""
    return exists().where(host_id_column == models.Host.id, *conditions).correlate(models.Host)


def test_label_predicate(project_id: int, labels: Sequence[str]) -> ColumnElement:
    """Host has a host test carrying one of ``labels`` (exact, any status).

    A correlated ``EXISTS`` like ``host_has_port``: a semi-join un-negated,
    an anti-join under ``NOT`` — never ``Host.id IN (subquery)``.
    """
    wanted = [v for v in labels if v]
    if not wanted:
        return false()
    return (
        exists()
        .where(
            HostTest.host_id == models.Host.id,
            HostTest.project_id == project_id,
            HostTest.label.in_(wanted),
        )
        .correlate(models.Host)
    )


def has_open_ports_predicate(db: Session) -> ColumnElement:
    """Host has at least one ``open`` port."""
    return host_has_port(*port_match_conditions(require_open=True))


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
    return _host_has(models.WebInterface.host_id, or_(*conditions))


def has_web_interface_predicate(db: Session) -> ColumnElement:
    """Host has at least one web interface row."""
    return _host_has(models.WebInterface.host_id)


def _web_text_predicate(db: Session, column, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose ``column`` ILIKE-matches any value."""
    conditions = [column.ilike(f'%{escape_like(v)}%', escape='\\') for v in values]
    return _host_has(models.WebInterface.host_id, or_(*conditions))


def header_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose ``server_header`` matches any value."""
    return _web_text_predicate(db, models.WebInterface.server_header, values)


def webtitle_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Host has a web interface whose page ``title`` matches any value."""
    return _web_text_predicate(db, models.WebInterface.title, values)


# ---------------------------------------------------------------------------
# Systemic-condition predicates (the `has:` weakness family)
# ---------------------------------------------------------------------------
#
# These back the drill-down from Systemic / Subnet Insights: a blind-spot row
# ("SMB signing disabled — 40 hosts") links to `/hosts?q=has:smb_unsigned`, and
# this predicate must resolve those same 40 hosts.  SMB-signing and cleartext
# are simple column/port conditions expressed directly in SQL here (index- and
# NOT-friendly at scale); EOL OS, cert hygiene, and weak auth carry non-trivial
# judgments (regex catalog, latest-observation-wins) that live once in
# host_condition_sets and are pulled in as id-sets so the two surfaces agree.


def smb_unsigned_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host whose recorded SMB-signing posture does not REQUIRE signing —
    ``not_required`` or ``disabled``, i.e. open to NTLM relay (v2.387.0; it
    matched ``disabled`` only, so every host nmap reported "enabled but not
    required" was missed).  Project-scoped.

    Expressed as an id-subquery rather than a bare ``Host.smb_signing IN …``
    so ``NOT has:smb_unsigned`` includes hosts whose signing posture is
    unknown (NULL) instead of silently dropping them via the NOT-IN/NULL
    footgun."""
    _H = aliased(models.Host)
    sub = db.query(_H.id).filter(
        _H.project_id == project_id, _H.smb_signing.in_(smb_signing_states.RELAYABLE)
    )
    return models.Host.id.in_(sub)


def cleartext_predicate(db: Session) -> ColumnElement:
    """Host with at least one OPEN cleartext-credential port (Telnet/FTP/POP/IMAP).

    Reuses ``port_match_subquery`` so port-in-set AND open are required on the
    SAME Port row — matching the systemic ``cleartext_services`` condition."""
    return host_has_port(*port_match_conditions(ports=sorted(CLEARTEXT_PORTS), require_open=True))


def eol_os_predicate(db: Session, project_id: int, only_host_ids=None) -> ColumnElement:
    """Host running an end-of-life OS (per the shared EOL catalog).

    ``only_host_ids`` (here and on the three predicates like it): the caller
    is labelling those hosts, so the judgment reads their rows only."""
    ids = eol_os_host_ids(db, project_id, only_host_ids)
    return models.Host.id.in_(ids) if ids else false()


def cert_issue_predicate(db: Session, project_id: int, only_host_ids=None) -> ColumnElement:
    """Host whose latest TLS cert observation is expired or self-signed."""
    ids = cert_issue_host_ids(db, project_id, host_ids=only_host_ids)
    return models.Host.id.in_(ids) if ids else false()


def weak_auth_predicate(db: Session, project_id: int, only_host_ids=None) -> ColumnElement:
    """Host where a guest / anonymous / null-session login succeeded."""
    ids = weak_auth_host_ids(db, project_id, only_host_ids)
    return models.Host.id.in_(ids) if ids else false()


def _netexec_flag_predicate(db: Session, project_id: int, column) -> ColumnElement:
    """Host with a NetExec / SMBMap result where ``column`` is true.  An
    id-subquery, so ``NOT has:…`` keeps hosts with no such result."""
    _H = aliased(models.Host)
    sub = (
        db.query(NetexecResult.host_id)
        .join(_H, NetexecResult.host_id == _H.id)
        .filter(_H.project_id == project_id, column.is_(True))
    )
    return models.Host.id.in_(sub)


def local_admin_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host where a credential was a local administrator ("(Pwn3d!)") — v2.412.0."""
    return _netexec_flag_predicate(db, project_id, NetexecResult.local_admin)


def writable_share_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host with a share that granted WRITE (NetExec --shares, SMBMap) — v2.412.0."""
    return _netexec_flag_predicate(db, project_id, NetexecResult.writable_share)


def weak_tls_predicate(db: Session, project_id: int, only_host_ids=None) -> ColumnElement:
    """Host whose latest TLS observation offers a weak protocol (SSLv2/SSLv3/
    TLS 1.0/1.1)."""
    ids = weak_tls_host_ids(db, project_id, only_host_ids)
    return models.Host.id.in_(ids) if ids else false()


# ---------------------------------------------------------------------------
# Vulnerability / evidence predicates
# ---------------------------------------------------------------------------

# The vuln/notes/tested predicates below scope their child-table subquery to
# ``project_id`` via a join to Host.  Without it the subquery materializes the
# matching host-ids across EVERY project in the deployment before the outer
# ``Host.project_id`` filter trims them — a real perf trap on multi-project,
# Nessus-heavy installs (the global ``vulnerabilities``/``annotations`` tables).
# Results are identical (the outer filter already constrained them); only the
# query plan tightens.  An aliased Host (``aliased(models.Host)``) keeps the
# subquery's hosts_v2 distinct from the outer query's.

def cve_predicate(db: Session, values: Sequence[str], project_id: int) -> ColumnElement:
    """Host has a vulnerability whose ``cve_id`` ILIKE-matches any value
    (project-scoped)."""
    _H = aliased(models.Host)
    conditions = [
        Vulnerability.cve_id.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, or_(*conditions))
        .distinct()
    )
    return models.Host.id.in_(sub)


def vuln_predicate(db: Session, values: Sequence[str], project_id: int) -> ColumnElement:
    """Host has a vulnerability whose ``title`` ILIKE-matches any value
    (project-scoped)."""
    _H = aliased(models.Host)
    conditions = [
        Vulnerability.title.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, or_(*conditions))
        .distinct()
    )
    return models.Host.id.in_(sub)


def severity_predicate(db: Session, severities: Iterable[str], project_id: int) -> ColumnElement:
    """Host has a vulnerability of any of the given severities (upper-case
    ``CRITICAL``/``HIGH``/``MEDIUM``/``LOW``), project-scoped."""
    _H = aliased(models.Host)
    sev_list = [s.upper() for s in severities]
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, Vulnerability.severity.in_(sev_list))
        .distinct()
    )
    return models.Host.id.in_(sub)


def kind_predicate(db: Session, kinds: Iterable[str], project_id: int) -> ColumnElement:
    """Host with a weakness of any of the given kinds (v2.415.0):
    ``misconfiguration`` (a catalog check, whichever tool reported it),
    ``vulnerability`` (anything else rated low or worse), ``informational``."""
    _H = aliased(models.Host)
    wanted = {k.strip().lower() for k in kinds}
    conditions = []
    if "misconfiguration" in wanted:
        conditions.append(Vulnerability.check_id.isnot(None))
    if "vulnerability" in wanted:
        conditions.append(and_(Vulnerability.check_id.is_(None), Vulnerability.severity != "INFO"))
    if "informational" in wanted:
        conditions.append(and_(Vulnerability.check_id.is_(None), Vulnerability.severity == "INFO"))
    if not conditions:
        return false()
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, or_(*conditions))
        .distinct()
    )
    return models.Host.id.in_(sub)


def check_predicate(db: Session, checks: Iterable[str], project_id: int) -> ColumnElement:
    """Host with any of the given catalog checks (v2.415.0)."""
    _H = aliased(models.Host)
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, Vulnerability.check_id.in_([c.strip().lower() for c in checks]))
        .distinct()
    )
    return models.Host.id.in_(sub)


def has_exploit_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host has a vulnerability flagged exploitable (project-scoped).

    Source-agnostic: reads ``Vulnerability.exploitable``, which only the Nessus
    path populates today — the moment another scanner sets it, its rows match
    here with no change.  See ``exploit_on_port_predicate`` for the port-scoped
    variant."""
    _H = aliased(models.Host)
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, Vulnerability.exploitable.is_(True))
        .distinct()
    )
    return models.Host.id.in_(sub)


def critical_exploit_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host has a CRITICAL vulnerability that is itself flagged exploitable —
    severity and exploit on the SAME row, the Hosts page's "critical ·
    exploit" (``critical_exploitable_count``) and Worth-a-look tier 1.

    ``has:critical AND has:exploit`` is not this: it also matches a critical
    with no exploit beside a low that has one (MCP acceptance run 2 — 31
    hosts against 7)."""
    _H = aliased(models.Host)
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(
            _H.project_id == project_id,
            Vulnerability.exploitable.is_(True),
            Vulnerability.severity == VulnerabilitySeverity.CRITICAL,
        )
        .distinct()
    )
    return models.Host.id.in_(sub)


def exploit_on_port_predicate(
    db: Session, ports: Sequence[int], project_id: int
) -> ColumnElement:
    """Host has an exploitable finding whose port is one of ``ports`` — same-row
    correlation (the exploit AND the port are on the SAME vulnerability), so it
    does NOT reduce to ``port:X AND has:exploit`` (which matches a host with X
    open and an exploit on any *other* port).

    The inner join on ``port_id`` drops host-level (``port_id IS NULL``)
    exploitable findings — correct, since a host-level exploit isn't 'on a port'.
    ``ports`` are port NUMBERS (what the user types), matched via the joined Port
    row.  Source-agnostic on ``exploitable`` (see ``has_exploit_predicate``)."""
    port_ints = [int(p) for p in ports]
    if not port_ints:
        return false()
    _H = aliased(models.Host)
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .join(models.Port, models.Port.id == Vulnerability.port_id)
        .filter(
            _H.project_id == project_id,
            Vulnerability.exploitable.is_(True),
            models.Port.port_number.in_(port_ints),
        )
        .distinct()
    )
    return models.Host.id.in_(sub)


# ---------------------------------------------------------------------------
# Notes / tested predicates
# ---------------------------------------------------------------------------

def has_notes_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host has at least one note — directly, or on one of its ports
    (project-scoped).

    The annotations table pins each note to exactly one target (host, port,
    scan, …), so a note left on a host's port carries ``host_id = NULL``.
    Counting only direct host notes would miss those, so we union in the hosts
    reached through a port-level note."""
    _H1 = aliased(models.Host)
    _H2 = aliased(models.Host)
    host_noted = (
        db.query(AnnotationModel.host_id)
        .join(_H1, _H1.id == AnnotationModel.host_id)
        .filter(_H1.project_id == project_id, AnnotationModel.host_id.isnot(None))
    )
    port_noted = (
        db.query(models.Port.host_id)
        .join(AnnotationModel, AnnotationModel.port_id == models.Port.id)
        .join(_H2, _H2.id == models.Port.host_id)
        .filter(_H2.project_id == project_id)
    )
    return models.Host.id.in_(host_noted.union(port_noted))


def note_predicate(db: Session, values: Sequence[str], project_id: int) -> ColumnElement:
    """Host has a note whose ``body`` ILIKE-matches any value (project-scoped)."""
    _H = aliased(models.Host)
    conditions = [
        AnnotationModel.body.ilike(f'%{escape_like(v)}%', escape='\\')
        for v in values
    ]
    sub = (
        db.query(AnnotationModel.host_id)
        .join(_H, _H.id == AnnotationModel.host_id)
        .filter(_H.project_id == project_id, or_(*conditions))
        .distinct()
    )
    return models.Host.id.in_(sub)


def has_test_execution_predicate(db: Session, project_id: int) -> ColumnElement:
    """Executed testing is qualifying evidence, independent of task status."""
    return models.Host.id.in_(tested_host_ids(project_id))


def untouched_conditions(db: Session) -> List[ColumnElement]:
    """Nobody has touched the host: no review or assignment (any HostFollow),
    no note, no host test that was not dismissed, no evidence record, no
    finding endpoint.  The ONE definition —
    the "Worth a look" queue, ``has:untouched`` and the address terrain
    all use it (v2.426.0)."""
    from app.db.models_findings import FindingHost

    # review 2026-10-01 R21 — correlated NOT EXISTS, not ``NOT IN (subquery)``.
    # Postgres cannot plan ``NOT IN`` as an anti-join (a NULL in the list would
    # change the answer), so each of the five ran as a hashed list of EVERY
    # project's rows — and a per-row rescan once the list outgrew work_mem.
    # NOT EXISTS is an anti-join driven by this project's hosts.  The two are
    # the same predicate here because none of the lists can hold a NULL: four
    # of the columns are NOT NULL and the note list keeps its IS NOT NULL
    # filter (``tests/test_read_path_review.py`` pins old == new).
    def none_of(model, *conditions) -> ColumnElement:
        return ~(
            exists()
            .where(model.host_id == models.Host.id, *conditions)
            .correlate(models.Host)
        )

    return [
        none_of(HostFollow),
        none_of(AnnotationModel, AnnotationModel.host_id.isnot(None)),
        none_of(HostTest, HostTest.status != "dismissed"),
        none_of(EvidenceRecord),
        none_of(FindingHost),
    ]


def untouched_predicate(db: Session) -> ColumnElement:
    return and_(*untouched_conditions(db))


def has_plan_entry_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host has an active proposed or in-progress test."""
    return models.Host.id.in_(planned_host_ids(project_id))


# ---------------------------------------------------------------------------
# Tag / label predicates (by id for the panel, by name for the DSL)
# ---------------------------------------------------------------------------

def tag_predicate_by_id(db: Session, tag_ids: Sequence[int]) -> ColumnElement:
    """Host carries any of the given tag IDs (OR)."""
    return _host_has(models.HostTagAssignment.host_id, models.HostTagAssignment.tag_id.in_(list(tag_ids)))


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


def site_predicate(db: Session, names: Sequence[str]) -> ColumnElement:
    """Host sits in a subnet belonging to any of the named sites.

    "Any subnet" semantics: a host in an overlapping range counts for every
    site its subnets belong to — deliberately broader than the single
    ``primary_site`` shown in the list, so the filter never hides a host that
    legitimately belongs to the selected site through one of its ranges."""
    return _host_has(
        models.HostSubnetMapping.host_id,
        models.Subnet.id == models.HostSubnetMapping.subnet_id,
        models.Subnet.site.in_(names),
    )


def site_none_predicate(db: Session, project_id: int) -> ColumnElement:
    """Host sits in a scoped subnet but NO subnet it maps to carries a site —
    the posture matrix's "Unassigned" column (v2.372.0).

    Site is inherited from the nearest site-bearing subnet
    (``subnet_insight_service.resolve_host_locations``), so a host is unassigned
    exactly when none of its mapped subnets has one.  A host outside every
    scoped subnet is NOT unassigned — it is unmapped, and absent from that
    matrix — so this is not simply the negation of ``site_predicate``."""
    mapped = (
        db.query(models.HostSubnetMapping.host_id)
        .join(models.Subnet, models.Subnet.id == models.HostSubnetMapping.subnet_id)
        .join(models.Scope, models.Scope.id == models.Subnet.scope_id)
        .filter(models.Scope.project_id == project_id)
    )
    # Same test as the inheritance rule: a non-blank site NAME (name and
    # site_id are always written together, scopes.py).
    sited = mapped.filter(func.trim(func.coalesce(models.Subnet.site, "")) != "")
    return and_(
        models.Host.id.in_(mapped.distinct()),
        models.Host.id.notin_(sited.distinct()),
    )


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
    """Review-status predicate — review is a SHARED, host-level state.

    Review is a team activity: a host is "being reviewed" if ANY teammate
    has it In Review, and "reviewed" if ANY teammate marked it Reviewed.  The
    filter answers team-level questions, not per-user ones:

      * ``none`` → no teammate has this host In Review or Reviewed (nobody is
        looking at it yet).  This is the fix for the "Not Reviewed shows hosts
        that are in review" bug — the old per-caller ``none`` returned hosts
        another teammate was actively reviewing.
      * ``in_review`` / ``in_review_any`` → some teammate has it In Review.
      * ``reviewed`` → some teammate has marked it Reviewed.

      * ``mine`` → the CALLER has it In Review (v2.450.0); "assigned to me"
        is the ``assigned`` filter.
      * ``revisit`` → a finished review of the CALLER'S that is not done
        (v2.451.0, ``my_review_followup_predicate``).  These two are the only
        per-user values.  Any
    other value (the retired ``watching`` follow state) falls through to the
    caller's own row so a legacy saved view / DSL query still resolves.
    """
    review_states = (FollowStatus.IN_REVIEW.value, FollowStatus.REVIEWED.value)
    if status == "none":
        return ~_host_has(HostFollow.host_id, HostFollow.status.in_(review_states))
    if status in ("in_review", "in_review_any"):
        in_review = db.query(HostFollow.host_id).filter(
            HostFollow.status == FollowStatus.IN_REVIEW.value
        )
        return models.Host.id.in_(in_review)
    if status == "reviewed":
        reviewed = db.query(HostFollow.host_id).filter(
            HostFollow.status == FollowStatus.REVIEWED.value
        )
        return models.Host.id.in_(reviewed)
    if status == "mine":
        # The CALLER has it In Review — exactly Operations' "In review" group
        # (``compute_my_attention_queue``), so its count opens its list.
        mine = db.query(HostFollow.host_id).filter(
            HostFollow.user_id == current_user.id,
            HostFollow.status == FollowStatus.IN_REVIEW.value,
        )
        return models.Host.id.in_(mine)
    if status == "revisit":
        # A finished review of the CALLER'S that is not done — exactly
        # Operations' "Changed since review" list.
        return my_review_followup_predicate(current_user)
    # Legacy per-user fallback (e.g. the retired 'watching' state).
    follow_ids = db.query(HostFollow.host_id).filter(
        HostFollow.user_id == current_user.id, HostFollow.status == status
    )
    return models.Host.id.in_(follow_ids)


def review_conclusion_predicate(db: Session, conclusions: Sequence[str]) -> ColumnElement:
    """Host whose review CONCLUDED one of these (v2.373.0) — team-level, like
    ``follow_predicate``: any teammate's Reviewed row counts.  A conclusion
    left on a row that has since gone back to In Review does not: the review
    is open again, so nothing is concluded.  ``needs_evidence`` is the
    Posture overview's "still needs evidence" count, and this is its list."""
    concluded = db.query(HostFollow.host_id).filter(
        HostFollow.status == FollowStatus.REVIEWED.value,
        HostFollow.review_conclusion.in_(list(conclusions)),
    )
    return models.Host.id.in_(concluded)


# --- changed since review ---------------------------------------------------
# What "the host changed after it was reviewed" means, ONCE: an open port
# first seen after ``HostFollow.reviewed_at``, or a critical / high scanner
# observation recorded after it.  Operations' "Changed since review" queue
# (``operations_read_service.compute_review_followups``, the CALLER'S reviews
# since v2.451.0) builds its rows from these two conditions, and so do
# ``follow:revisit`` (the caller's — the list that queue opens) and
# ``has:changed_since_review`` (anyone's review), so a count and the Hosts
# list it opens cannot disagree.  Measured
# against ``reviewed_at`` — never ``updated_at``, which every view bumps.  A
# review with no baseline (NULL) has nothing to be "after".

def port_after_review_condition(follow=HostFollow) -> ColumnElement:
    """An open ``Port`` row first seen after ``follow``'s review."""
    return and_(
        follow.reviewed_at.isnot(None),
        models.Port.state == "open",
        models.Port.first_seen > follow.reviewed_at,
    )


def vuln_after_review_condition(follow=HostFollow) -> ColumnElement:
    """A critical / high ``Vulnerability`` row recorded after ``follow``'s review."""
    return and_(
        follow.reviewed_at.isnot(None),
        Vulnerability.severity.in_((VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH)),
        Vulnerability.created_at > follow.reviewed_at,
    )


def changed_since_review_predicate(db: Session) -> ColumnElement:
    """Host with a finished review (anyone's) that it changed after."""
    hf = aliased(HostFollow)
    new_port = (
        exists()
        .where(models.Port.host_id == hf.host_id, port_after_review_condition(hf))
        .correlate(hf)
    )
    new_vuln = (
        exists()
        .where(Vulnerability.host_id == hf.host_id, vuln_after_review_condition(hf))
        .correlate(hf)
    )
    return (
        exists()
        .where(
            hf.host_id == models.Host.id,
            hf.status == FollowStatus.REVIEWED.value,
            or_(new_port, new_vuln),
        )
        .correlate(models.Host)
    )


def my_review_followup_predicate(current_user: User) -> ColumnElement:
    """Host with a finished review of the CALLER'S that is not the end of the
    matter (v2.451.0, ``follow:revisit``): that review concluded "needs more
    evidence", or the host changed after it.  Both halves are tested on the
    SAME follow row — the caller's — which is why this is one predicate and
    not ``has:changed_since_review OR conclusion:needs_evidence`` narrowed by
    a "reviewed by me" value: a host the caller reviewed cleanly, and that
    changed after a TEAMMATE'S older review, is not the caller's to re-check.

    Operations' "Changed since review" (``compute_review_followups``) builds
    its rows from the same conditions on the same rows, so the section's count
    is the length of this list.  One correlated EXISTS."""
    hf = aliased(HostFollow)
    new_port = (
        exists()
        .where(models.Port.host_id == hf.host_id, port_after_review_condition(hf))
        .correlate(hf)
    )
    new_vuln = (
        exists()
        .where(Vulnerability.host_id == hf.host_id, vuln_after_review_condition(hf))
        .correlate(hf)
    )
    return (
        exists()
        .where(
            hf.host_id == models.Host.id,
            hf.user_id == current_user.id,
            hf.status == FollowStatus.REVIEWED.value,
            or_(hf.review_conclusion == "needs_evidence", new_port, new_vuln),
        )
        .correlate(models.Host)
    )


def assigned_predicate(db: Session, value: str, current_user: User) -> Optional[ColumnElement]:
    """Assignment predicate: ``any`` → assigned to anyone, ``none`` → assigned
    to nobody, ``me`` → the caller, a **username** (the normal case — user ids
    aren't surfaced in the UI), or a numeric user id.  Returns ``None`` for an unusable value so callers skip the
    filter (legacy parity).

    "Assigned" keys on ``assigned_at`` (cleared on unassign).  Taking a host
    In Review now sets ``assigned_at`` too (see the review-status write path),
    so "review it = it's yours" holds without conflating the two here."""
    if value in ("any", "none"):
        assigned = db.query(HostFollow.host_id).filter(HostFollow.assigned_at.isnot(None))
        # `none` is the complement, and it is the half operators actually reach
        # for: "critical findings nobody owns" is a work-allocation question,
        # where "assigned to someone" is rarely the interesting set. Its absence
        # made that question expressible only as `NOT assigned:any`, while the
        # sibling `follow:` field accepted `none` — so the obvious phrasing
        # errored on one field and worked on the other (v2.291.0).
        return (
            models.Host.id.in_(assigned)
            if value == "any"
            else ~models.Host.id.in_(assigned)
        )
    if value == "me":
        assignee_id: Optional[int] = current_user.id
    elif value.isdigit():
        assignee_id = int(value)
    else:
        # A username — the value a user actually knows and types (ids aren't
        # shown anywhere). Case-insensitive exact match.
        row = (
            db.query(User.id)
            .filter(func.lower(User.username) == value.lower())
            .first()
        )
        assignee_id = row[0] if row else None
    if assignee_id is None:
        return None
    assigned = db.query(HostFollow.host_id).filter(
        HostFollow.user_id == assignee_id, HostFollow.assigned_at.isnot(None)
    )
    return models.Host.id.in_(assigned)


def stale_review_predicate(db: Session) -> ColumnElement:
    """Hosts marked Reviewed (by anyone) that a scan has re-observed SINCE the
    review — ``last_seen`` is later than the reviewed follow's timestamp, so the
    review is stale and worth re-checking (§9 'new evidence since review').
    A fresh follow row has updated_at=NULL (onupdate-only), so fall back to
    created_at for the review time."""
    hf = aliased(HostFollow)
    review_ts = func.coalesce(hf.updated_at, hf.created_at)
    return exists().where(
        (hf.host_id == models.Host.id)
        & (hf.status == FollowStatus.REVIEWED.value)
        & (models.Host.last_seen.isnot(None))
        & (models.Host.last_seen > review_ts)
    )


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


# ---------------------------------------------------------------------------
# Time windows (v2.363.0) — "what changed since my last visit", as host sets.
#
# ONE definition, used twice: the Operations "since your last visit" counts
# (workbench_service.compute_since_last_visit) and the DSL fields those counts link
# to (firstseen: / changedsince: / vulnsince:).  A count that opens a list
# derived some other way is how "12 new hosts" comes to open 9.
#
# A window is (start, end]: strictly after the cursor the analyst last
# acknowledged, up to and including the snapshot they were shown.  ``end`` is
# optional (open-ended = "until now").
# ---------------------------------------------------------------------------

def _in_window(column, start, end) -> ColumnElement:
    cond = column > start
    if end is not None:
        cond = cond & (column <= end)
    return cond


def first_seen_window_predicate(start, end=None) -> ColumnElement:
    """Hosts FIRST observed in the window — new records."""
    return _in_window(models.Host.first_seen, start, end)


def vuln_window_condition(start, end=None, severities: Optional[Iterable[str]] = None) -> ColumnElement:
    """Row-level: a scanner observation recorded in the window, optionally of
    the given severities (lower-case).  Severity is matched the way the
    Operations counter always has — the enum cast to text, lowered — so the
    count and the list cannot disagree on a casing quirk."""
    cond = _in_window(Vulnerability.created_at, start, end)
    if severities:
        sev_col = func.lower(cast(Vulnerability.severity, SAString))
        cond = cond & sev_col.in_([s.lower() for s in severities])
    return cond


def vuln_window_predicate(
    db: Session, project_id: int, start, end=None, severities: Optional[Iterable[str]] = None,
) -> ColumnElement:
    """Hosts carrying a scanner observation recorded in the window.  Severity
    and time are matched on the SAME row: `has:critical` AND "something new"
    would also match a host whose only new row is informational."""
    _H = aliased(models.Host)
    sub = (
        db.query(Vulnerability.host_id)
        .join(_H, _H.id == Vulnerability.host_id)
        .filter(_H.project_id == project_id, vuln_window_condition(start, end, severities))
        .distinct()
    )
    return models.Host.id.in_(sub)


def changed_window_predicate(db: Session, project_id: int, start, end=None) -> ColumnElement:
    """EXISTING hosts (first observed at or before ``start``) that gained a
    port or a scanner observation in the window — a material change to a
    target the analyst already knew, as opposed to a new record.  Disjoint
    from ``first_seen_window_predicate`` by construction.

    Removed ports are not detectable (the dedup keeps ports and does not track
    per-scan presence — same limit as host_change_service)."""
    new_port = (
        db.query(models.Port.host_id)
        .filter(_in_window(models.Port.first_seen, start, end))
        .distinct()
    )
    return (
        (models.Host.first_seen <= start)
        & (
            models.Host.id.in_(new_port)
            | vuln_window_predicate(db, project_id, start, end)
        )
    )


def attribution_org_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Hosts whose registered netblock owner matches any value (substring).

    The scope question a pentest turns on: today's out-of-scope check only
    compares hosts against CIDRs typed into the scope, which validates a
    spreadsheet against itself. ``NOT org:"Acme"`` asks the far more useful
    question — what did we touch that isn't registered to the client?
    """
    from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution

    if not values:
        return false()
    clauses = [
        NetworkAttribution.org_name.ilike(f"%{escape_like(v)}%", escape="\\")
        for v in values if v
    ]
    if not clauses:
        return false()
    sub = (
        db.query(HostNetworkAttribution.host_id)
        .join(
            NetworkAttribution,
            NetworkAttribution.id == HostNetworkAttribution.attribution_id,
        )
        .filter(or_(*clauses))
        .distinct()
    )
    return models.Host.id.in_(sub)


def attribution_asn_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Hosts in any of the given autonomous systems. ``AS`` prefix optional."""
    from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution

    asns = []
    for v in values or []:
        text = str(v).strip().upper().lstrip("AS")
        if text.isdigit():
            asns.append(int(text))
    if not asns:
        return false()
    sub = (
        db.query(HostNetworkAttribution.host_id)
        .join(
            NetworkAttribution,
            NetworkAttribution.id == HostNetworkAttribution.attribution_id,
        )
        .filter(NetworkAttribution.asn.in_(asns))
        .distinct()
    )
    return models.Host.id.in_(sub)


def attribution_cloud_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Hosts hosted by a given cloud provider (``aws``/``azure``/``gcp``/…).

    ``cloud:none`` selects hosts with attribution but NO cloud provider — i.e.
    on-premise or a provider we don't have prefixes for — which is what an
    operator wants when asking "what isn't in the client's cloud tenancy?".
    """
    from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution

    if not values:
        return false()
    wants_none = any(str(v).strip().lower() in ("none", "null") for v in values)
    named = [str(v).strip().lower() for v in values
             if str(v).strip().lower() not in ("none", "null")]

    base = (
        db.query(HostNetworkAttribution.host_id)
        .join(
            NetworkAttribution,
            NetworkAttribution.id == HostNetworkAttribution.attribution_id,
        )
    )
    clauses = []
    if named:
        clauses.append(func.lower(NetworkAttribution.cloud_provider).in_(named))
    if wants_none:
        clauses.append(NetworkAttribution.cloud_provider.is_(None))
    if not clauses:
        return false()
    return models.Host.id.in_(base.filter(or_(*clauses)).distinct())


def attribution_country_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Hosts whose registered netblock is in any of the given countries.

    RDAP stores an ISO-3166 alpha-2 code (``US``, ``NL``), so this is an
    exact case-insensitive match — ``country:US`` — not a substring like
    ``org:``. ``NOT country:US`` is the useful scope-validation query: what did
    we touch that isn't registered where the client operates?
    """
    from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution

    codes = [str(v).strip().upper() for v in (values or []) if str(v).strip()]
    if not codes:
        return false()
    sub = (
        db.query(HostNetworkAttribution.host_id)
        .join(
            NetworkAttribution,
            NetworkAttribution.id == HostNetworkAttribution.attribution_id,
        )
        .filter(func.upper(NetworkAttribution.country).in_(codes))
        .distinct()
    )
    return models.Host.id.in_(sub)


def cert_org_predicate(db: Session, values: Sequence[str]) -> ColumnElement:
    """Hosts presenting a certificate whose subject Organization matches.

    Distinct from ``org:`` (registry attribution): a CA *validated* this claim
    before issuing, where a registry record is self-declared. Where the two
    disagree — cert says one company, registration says another — that
    disagreement is itself worth surfacing.

    Only OV/EV certificates carry an Organization; DV certs (most of the
    modern web) have none, so a non-match means "no claim made", not "not the
    client's".
    """
    if not values:
        return false()
    clauses = [
        models.WebInterface.cert_subject_org.ilike(f"%{escape_like(v)}%", escape="\\")
        for v in values if v
    ]
    if not clauses:
        return false()
    sub = (
        db.query(models.WebInterface.host_id)
        .filter(or_(*clauses))
        .distinct()
    )
    return models.Host.id.in_(sub)
