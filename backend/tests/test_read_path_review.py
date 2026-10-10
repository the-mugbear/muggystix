"""Read-path fixes from the 2026-10-01 review (R18–R24, N9 indexes).

Every one of these rewrote HOW a list or count is produced, never WHAT it
returns.  So each test either pins the rewritten predicate against the
predicate it replaced — written out here as it stood — over rows chosen to
separate the two, or counts the statements the rewrite removed.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import String, cast, event, func, literal, or_
from sqlalchemy.orm import aliased
from sqlalchemy.sql import exists

from app.api.v1.endpoints import hosts as hosts_endpoint
from app.api.v1.endpoints import scopes as scopes_endpoint
from app.db import models
from app.db.models import Annotation, HostFollow, HostSubnetMapping, Scope, Site, Subnet
from app.db.models_attribution import HostNetworkAttribution, NetworkAttribution
from app.db.models_auth import User, UserRole
from app.db.models_confidence import NetexecResult
from app.db.models_findings import Finding, FindingHost
from app.db.models_host_tests import HostTest
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services import host_query_predicates as P
from app.services import scanner_observation_service as observations
from app.services.host_query import build_filtered_host_query, build_search_predicate
from app.services.host_query_common import escape_like
from app.services.host_query_dsl import negate
from app.services.host_test_queries import planned_host_ids, tested_host_ids
from app.services.subnet_correlation import SubnetCorrelationService
from app.services.systemic_insight_service import compute_systemic_insights


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@contextmanager
def statements(db_session):
    """The SQL statements run inside the block."""
    seen = []
    bind = db_session.get_bind()
    engine = getattr(bind, "engine", bind)

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _is_postgres(db_session) -> bool:
    return db_session.get_bind().dialect.name == "postgresql"


def _project(db_session, name):
    project = Project(name=name, slug=name)
    db_session.add(project)
    db_session.flush()
    return project


def _host(db_session, project_id, ip, **extra):
    host = models.Host(project_id=project_id, ip_address=ip, state="up", **extra)
    db_session.add(host)
    db_session.flush()
    return host


def _scan(db_session, project_id, tool="nmap"):
    scan = models.Scan(project_id=project_id, filename=f"{tool}.xml", tool_name=tool, scan_type=tool)
    db_session.add(scan)
    db_session.flush()
    return scan


def _port(db_session, host, number, state="open", **service):
    port = models.Port(host_id=host.id, port_number=number, protocol="tcp", state=state, **service)
    db_session.add(port)
    db_session.flush()
    return port


def _ids(db_session, project_id, predicate):
    return {
        row[0] for row in
        db_session.query(models.Host.id)
        .filter(models.Host.project_id == project_id, predicate).all()
    }


# ---------------------------------------------------------------------------
# R19 — port substring search: project-scoped, one indexable arm
# ---------------------------------------------------------------------------

def _old_version_predicate(db, values, states=None):
    """``version_predicate`` as it stood before R19: three ORed arms, concat()."""
    joined = func.concat(
        func.coalesce(models.Port.service_product, ""), " ", func.coalesce(models.Port.service_version, ""),
    )
    conds = [
        or_(
            models.Port.service_product.ilike(f"%{escape_like(v)}%", escape="\\"),
            models.Port.service_version.ilike(f"%{escape_like(v)}%", escape="\\"),
            joined.ilike(f"%{escape_like(v)}%", escape="\\"),
        )
        for v in values if v
    ]
    resolved = P.resolve_endpoint_states(states, has_endpoint=True)
    sub = db.query(models.Port.host_id).filter(
        *([P.port_state_condition(resolved)] if resolved else []), or_(*conds),
    )
    return models.Host.id.in_(sub)


@pytest.fixture
def port_estate(db_session, test_project):
    pid = test_project.id
    other = _project(db_session, "r19-other")
    hosts = {}
    rows = [
        ("10.9.0.1", 22, "open", dict(service_name="ssh", service_product="OpenSSH", service_version="7.4")),
        ("10.9.0.2", 22, "open", dict(service_name="ssh", service_product="OpenSSH", service_version=None)),
        ("10.9.0.3", 22, "open", dict(service_name="ssh", service_product=None, service_version="7.4p1")),
        ("10.9.0.4", 80, "open", dict(service_name="http", service_product="nginx", service_version="1.14.0")),
        ("10.9.0.5", 80, "closed", dict(service_name="http", service_product="OpenSSH", service_version="7.4")),
        ("10.9.0.6", 8080, "open", dict(service_name=None, service_product=None, service_version=None)),
        ("10.9.0.7", 443, "open", dict(service_name="https", service_product="100%_real", service_version="a_b")),
        ("10.9.0.8", 21, "filtered", dict(service_name="ftp", service_product="vsftpd", service_version="3.0.3")),
    ]
    for ip, number, state, service in rows:
        hosts[ip] = _host(db_session, pid, ip)
        _port(db_session, hosts[ip], number, state, **service)
    hosts["none"] = _host(db_session, pid, "10.9.0.99")  # no port at all
    # The same services in ANOTHER project.
    foreign = _host(db_session, other.id, "10.9.0.1")
    _port(db_session, foreign, 22, "open", service_name="ssh", service_product="OpenSSH", service_version="7.4")
    return pid, other.id, hosts, foreign


@pytest.mark.parametrize("values,states", [
    (["OpenSSH"], None), (["openssh 7.4"], None), (["7.4"], None), (["7.4p1"], None),
    (["SSH 7"], None), (["nginx", "vsftpd"], None), (["OpenSSH"], ["closed"]),
    (["vsftpd"], ["any"]), (["%"], None), (["_"], None), (["a_b"], None), ([" "], None),
    (["nothing-matches"], None), ([""], None),
])
def test_version_predicate_matches_what_the_three_arm_form_matched(db_session, port_estate, values, states):
    pid, _other, _hosts, _foreign = port_estate
    old = _ids(db_session, pid, _old_version_predicate(db_session, values, states)) if any(values) else set()
    assert _ids(db_session, pid, P.version_predicate(db_session, values, states)) == old
    assert _ids(db_session, pid, ~P.version_predicate(db_session, values, states)) == (
        _ids(db_session, pid, ~_old_version_predicate(db_session, values, states)) if any(values)
        else _ids(db_session, pid, models.Host.id.isnot(None))
    )


def test_product_version_text_is_what_the_index_is_built_on(db_session):
    """``c3f6b9d2e5a7`` indexes this expression; if the two drift apart the
    index silently stops being used."""
    if not _is_postgres(db_session):
        pytest.skip("the expression index is Postgres-only")
    rendered = str(P.product_version_text().compile(
        dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True},
    ))
    assert rendered == (
        "coalesce(ports_v2.service_product, '') || ' ' || coalesce(ports_v2.service_version, '')"
    )
    migration = _load_migration()
    assert migration._PRODUCT_VERSION_EXPR == rendered.replace("ports_v2.", "").join("()")


# (``P.port_match_subquery`` — the id list those predicates were built on, and
# its project-scoped form — was removed in the 2026-10-08 review with its last
# caller, the agents' host filter, which now runs the page's own assembly.  Its
# old == new cases remain below against a copy of the old form.)

@pytest.mark.parametrize("term", ["ssh", "OpenSSH", "22", "nginx", "10.9.0", "http", "zzz"])
def test_scoped_search_matches_the_unscoped_search(db_session, port_estate, term):
    pid, _other, _hosts, _foreign = port_estate
    assert (
        _ids(db_session, pid, build_search_predicate(db_session, term, pid))
        == _ids(db_session, pid, build_search_predicate(db_session, term))
    )


def _old_port_predicate(db, **kwargs):
    """The port predicates as they stood before R19: ``Host.id IN`` an
    uncorrelated join of every project's hosts and ports."""
    return models.Host.id.in_(
        db.query(models.Host.id).join(models.Port).filter(*P.port_match_conditions(**kwargs))
    )


@pytest.mark.parametrize("name,new,old_kwargs", [
    ("port", lambda db: P.port_predicate(db, [22, 80]), dict(ports=[22, 80])),
    ("port any", lambda db: P.port_predicate(db, [80], ["any"]), dict(ports=[80], port_states=["any"])),
    ("service", lambda db: P.service_predicate(db, ["ssh", "ftp"]), dict(services=["ssh", "ftp"])),
    ("service closed", lambda db: P.service_predicate(db, ["http"], ["closed"]),
     dict(services=["http"], port_states=["closed"])),
    ("portstate", lambda db: P.portstate_predicate(db, ["filtered", "closed"]),
     dict(port_states=["filtered", "closed"])),
    ("open", lambda db: P.has_open_ports_predicate(db), dict(require_open=True)),
    ("cleartext", lambda db: P.cleartext_predicate(db), dict(ports=sorted(P.CLEARTEXT_PORTS), require_open=True)),
])
def test_port_predicates_as_exists_select_what_in_selected(db_session, port_estate, name, new, old_kwargs):
    pid, other_id, _hosts, _foreign = port_estate
    for project_id in (pid, other_id):
        assert _ids(db_session, project_id, new(db_session)) == _ids(
            db_session, project_id, _old_port_predicate(db_session, **old_kwargs)), name
        # … and negated, which is where NOT IN and NOT EXISTS would part ways.
        assert _ids(db_session, project_id, ~new(db_session)) == _ids(
            db_session, project_id, ~_old_port_predicate(db_session, **old_kwargs)), name


@pytest.mark.parametrize("kwargs", [
    dict(services="ssh"), dict(ports="22"), dict(ports="22,80", services="http", port_states="any"),
    dict(has_open_ports=True), dict(has_open_ports=False), dict(port_states="closed"),
    dict(ports="80", has_open_ports=True, port_states="closed"),
    dict(q="NOT has:open_ports"), dict(q="NOT service:ssh AND NOT port:80"),
    dict(q='version:"OpenSSH 7" OR has:cleartext'),
])
def test_port_filters_never_reach_postgres_as_in_or_not_in(db_session, test_user, port_estate, kwargs):
    """A port filter is a correlated EXISTS on the host: no uncorrelated scan
    of every project's ports, and above all no ``NOT IN (subquery)``."""
    pid, _other, _hosts, _foreign = port_estate
    query = build_filtered_host_query(db_session, test_user, project_id=pid, **kwargs).with_entities(models.Host.id)
    sql = str(query.statement.compile(dialect=db_session.get_bind().dialect))
    assert "EXISTS (SELECT" in sql and "ports_v2.host_id = hosts_v2.id" in sql, sql
    assert " IN (SELECT" not in sql, sql
    assert sql.count("FROM hosts_v2") == 1, sql   # the port subqueries do not re-join hosts
    query.all()  # and it runs


def test_search_keeps_its_port_subquery_inside_the_project(db_session, test_user, port_estate):
    pid, _other, _hosts, _foreign = port_estate
    sql = str(build_filtered_host_query(db_session, test_user, project_id=pid, search="ssh")
              .with_entities(models.Host.id).statement.compile(dialect=db_session.get_bind().dialect))
    # once for the outer query, once inside the port subquery
    assert sql.count("hosts_v2.project_id") == 2, sql


# ---------------------------------------------------------------------------
# Review 2026-10-07 — the other child tables as EXISTS
# ---------------------------------------------------------------------------

@pytest.fixture
def child_estate(db_session, test_project, test_user):
    """Hosts with a web path, a web interface, a tag, a site and a review —
    one of each — plus a host with nothing, a web row with NO host, and the
    same rows in another project."""
    pid = test_project.id
    other = _project(db_session, "exists-other")
    tag_ids = {}
    for project_id in (pid, other.id):
        scan = _scan(db_session, project_id, tool="httpx")
        scope = Scope(project_id=project_id, name="s")
        tag = models.HostTag(project_id=project_id, name="crown")
        db_session.add_all([scope, tag])
        db_session.flush()
        tag_ids[project_id] = tag.id
        subnet = Subnet(scope_id=scope.id, cidr="10.7.0.0/24", site="HQ")
        db_session.add(subnet)
        db_session.flush()
        hosts = [_host(db_session, project_id, f"10.7.0.{n}") for n in range(1, 7)]
        db_session.add(models.WebPath(project_id=project_id, host_id=hosts[0].id, scan_id=scan.id,
                                      source="ffuf", url="http://10.7.0.1/admin", path="/admin"))
        db_session.add(models.WebInterface(
            scan_id=scan.id, project_id=project_id, host_id=hosts[1].id, source="httpx",
            url="http://10.7.0.2/", server_header="nginx/1.14", technologies=["WordPress"]))
        db_session.add(models.WebInterface(   # an interface nobody tied to a host
            scan_id=scan.id, project_id=project_id, host_id=None, source="httpx",
            url="http://orphan.example/", server_header="nginx/1.14", technologies=["WordPress"]))
        db_session.add(models.HostTagAssignment(host_id=hosts[2].id, tag_id=tag.id))
        db_session.add(HostSubnetMapping(host_id=hosts[3].id, subnet_id=subnet.id))
        db_session.add(HostFollow(host_id=hosts[4].id, user_id=test_user.id,
                                  status=models.FollowStatus.REVIEWED))
    db_session.flush()
    return pid, other.id, tag_ids


def _old_in(db, column, *conditions, joins=()):
    """The predicates as they stood: ``Host.id IN`` an uncorrelated subquery
    over every project's rows."""
    sub = db.query(column)
    for target, on in joins:
        sub = sub.join(target, on)
    return models.Host.id.in_(sub.filter(column.isnot(None), *conditions))


@pytest.mark.parametrize("name", ["webpath", "tech", "has_web", "header", "tag", "site", "follow_none"])
def test_child_table_predicates_as_exists_select_what_in_selected(db_session, test_user, child_estate, name):
    pid, other_id, tag_ids = child_estate
    WI = models.WebInterface
    for project_id in (pid, other_id):
        new, old = {
            "webpath": (P.webpath_predicate(db_session, ["admin"]),
                        _old_in(db_session, models.WebPath.host_id, models.WebPath.path.ilike("%admin%"))),
            "tech": (P.tech_predicate(db_session, ["wordpress"]),
                     _old_in(db_session, WI.host_id, cast(WI.technologies, String).ilike("%wordpress%"))),
            "has_web": (P.has_web_interface_predicate(db_session), _old_in(db_session, WI.host_id)),
            "header": (P.header_predicate(db_session, ["nginx"]),
                       _old_in(db_session, WI.host_id, WI.server_header.ilike("%nginx%"))),
            "tag": (P.tag_predicate_by_id(db_session, [tag_ids[project_id]]),
                    _old_in(db_session, models.HostTagAssignment.host_id,
                            models.HostTagAssignment.tag_id.in_([tag_ids[project_id]]))),
            "site": (P.site_predicate(db_session, ["HQ"]),
                     _old_in(db_session, HostSubnetMapping.host_id, Subnet.site.in_(["HQ"]),
                             joins=[(Subnet, Subnet.id == HostSubnetMapping.subnet_id)])),
            "follow_none": (
                P.follow_predicate("none", test_user),
                ~_old_in(db_session, HostFollow.host_id, HostFollow.status.in_(("in_review", "reviewed")))),
        }[name]
        selected = _ids(db_session, project_id, new)
        assert selected == _ids(db_session, project_id, old), name
        assert 0 < len(selected) < 6, name     # the fixture really tells hosts apart
        # … and negated, where NOT IN and NOT EXISTS would part ways.
        assert _ids(db_session, project_id, ~new) == _ids(db_session, project_id, ~old), name


@pytest.mark.parametrize("q", [
    "NOT path:admin", "path:admin OR port:8080", "NOT tech:wordpress", "NOT has:web",
    "NOT header:nginx", "follow:none", "NOT site:HQ",
])
def test_child_table_filters_never_reach_postgres_as_in_or_not_in(db_session, test_user, child_estate, q):
    pid, _other, _tags = child_estate
    query = build_filtered_host_query(db_session, test_user, project_id=pid, q=q).with_entities(models.Host.id)
    sql = str(query.statement.compile(dialect=db_session.get_bind().dialect))
    assert "EXISTS (SELECT" in sql, sql
    assert " IN (SELECT" not in sql, sql
    query.all()


# ---------------------------------------------------------------------------
# R21 — "untouched" as NOT EXISTS
# ---------------------------------------------------------------------------

def _old_untouched_conditions(db):
    """``untouched_conditions`` as it stood before R21."""
    return [
        ~models.Host.id.in_(db.query(HostFollow.host_id)),
        ~models.Host.id.in_(db.query(Annotation.host_id).filter(Annotation.host_id.isnot(None))),
        ~models.Host.id.in_(db.query(HostTest.host_id).filter(HostTest.status != "dismissed")),
        ~models.Host.id.in_(db.query(EvidenceRecord.host_id)),
        ~models.Host.id.in_(db.query(FindingHost.host_id)),
    ]


def test_untouched_not_exists_selects_what_not_in_selected(db_session, test_project, test_user):
    pid = test_project.id
    scan = _scan(db_session, pid)

    def test(host, status, **extra):
        db_session.add(HostTest(
            project_id=pid, host_id=host.id, tool="nmap", description="d", rationale="r",
            priority="high", status=status, source="person",
            request_key=f"r21-{host.ip_address}-{status}", request_hash="0" * 64, **extra,
        ))

    untouched = _host(db_session, pid, "10.21.0.1")
    followed = _host(db_session, pid, "10.21.0.2")
    db_session.add(HostFollow(host_id=followed.id, user_id=test_user.id, status=models.FollowStatus.WATCHING))
    noted = _host(db_session, pid, "10.21.0.3")
    db_session.add(Annotation(host_id=noted.id, user_id=test_user.id, body="n"))
    planned = _host(db_session, pid, "10.21.0.4")
    test(planned, "proposed")
    dismissed_only = _host(db_session, pid, "10.21.0.5")
    test(dismissed_only, "dismissed", dismissed_reason="no")
    evidenced = _host(db_session, pid, "10.21.0.6")
    db_session.add(EvidenceRecord(project_id=pid, host_id=evidenced.id, tool="nmap", outcome="info", summary="s"))
    found = _host(db_session, pid, "10.21.0.7")
    finding = Finding(project_id=pid, title="f", severity="high", status="open", source="manual")
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=found.id))
    # A PORT-level note: its annotation row has host_id NULL.  It is the row
    # that makes ``NOT IN`` and ``NOT EXISTS`` differ if the IS NOT NULL filter
    # is ever lost — and it does not make its host "touched".
    port_noted = _host(db_session, pid, "10.21.0.8")
    port = _port(db_session, port_noted, 80)
    db_session.add(Annotation(host_id=None, port_id=port.id, user_id=test_user.id, body="p"))
    both = _host(db_session, pid, "10.21.0.9")
    test(both, "dismissed", dismissed_reason="no")
    test(both, "done", tester_summary="ran it")
    db_session.flush()
    assert scan.id  # the estate has a scan, as a real one would

    new = _ids(db_session, pid, P.untouched_predicate(db_session))
    assert new == _ids(db_session, pid, *[_and(_old_untouched_conditions(db_session))])
    assert new == {untouched.id, dismissed_only.id, port_noted.id}
    # The five conditions stay five separate conditions (the queue and the
    # terrain splat them into their own filters).
    assert len(P.untouched_conditions(db_session)) == 5

    sql = str(db_session.query(models.Host.id).filter(P.untouched_predicate(db_session))
              .statement.compile(dialect=db_session.get_bind().dialect))
    assert sql.count("NOT (EXISTS") == 5 and "NOT IN" not in sql
    # Correlated: no second, unjoined hosts_v2 inside the subqueries.
    assert sql.count("FROM hosts_v2") == 1


def _and(conditions):
    from sqlalchemy import and_
    return and_(*conditions)


# ---------------------------------------------------------------------------
# R20 — issue lookups without coalesce()
# ---------------------------------------------------------------------------

def _old_key():
    return func.coalesce(Vulnerability.issue_key, literal("row:") + cast(Vulnerability.id, String))


def test_issue_key_lookup_matches_the_coalesce_form(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid, "nessus")
    hosts = [_host(db_session, pid, f"10.20.0.{i}") for i in range(1, 5)]

    def vuln(host, **fields):
        row = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                            severity=VulnerabilitySeverity.HIGH, **fields)
        db_session.add(row)
        db_session.flush()
        return row

    cve_a = vuln(hosts[0], title="OpenSSL bug", cve_id="CVE-2024-0001")
    cve_b = vuln(hosts[1], title="OpenSSL bug (other wording)", cve_id="CVE-2024-0001")
    titled = vuln(hosts[2], title="Weak cipher suites")
    # No CVE, no usable title: the row is an issue of its own (issue_key NULL).
    anonymous = vuln(hosts[3], title="")
    anonymous_two = vuln(hosts[0], title="")
    db_session.flush()
    assert anonymous.issue_key is None and cve_a.issue_key == cve_b.issue_key

    def old(keys):
        return {r[0] for r in db_session.query(Vulnerability.id).filter(_old_key().in_(keys)).all()}

    def new(keys):
        return {r[0] for r in db_session.query(Vulnerability.id).filter(observations._key_in(keys)).all()}

    cases = [
        [cve_a.issue_key],
        [titled.issue_key],
        [f"row:{anonymous.id}"],
        [f"row:{anonymous.id}", f"row:{anonymous_two.id}", cve_a.issue_key, titled.issue_key],
        [f"row:{cve_a.id}"],            # a row that HAS a key is not its own "row:" issue
        [f"row:0{anonymous.id}"],       # not the form the key is written in
        ["row:"], ["row:abc"], ["row:-1"], ["row:999999999"],
        ["cve:CVE-1999-9999"], [],
    ]
    for keys in cases:
        assert new(keys) == old(keys), keys
    assert new([f"row:{anonymous.id}"]) == {anonymous.id}
    assert new([cve_a.issue_key]) == {cve_a.id, cve_b.id}
    assert new([f"row:{cve_a.id}"]) == set()

    # The lookup no longer wraps the column, so the new index can serve it.
    sql = str(observations._key_is(cve_a.issue_key).compile(dialect=db_session.get_bind().dialect))
    assert "coalesce" not in sql.lower()

    # And the public functions built on it answer as before.
    assert observations.issue_host_total(db_session, pid, cve_a.issue_key) == 2
    assert observations.issue_host_total(db_session, pid, f"row:{anonymous.id}") == 1
    assert [h.host_id for h in observations.issue_hosts(db_session, pid, cve_a.issue_key)] == [
        hosts[0].id, hosts[1].id,
    ]
    assert [h.host_id for h in observations.issue_hosts(db_session, pid, f"row:{anonymous_two.id}")] == [
        hosts[0].id,
    ]


# ---------------------------------------------------------------------------
# R22 — plugin monoculture grouped in SQL; coverage technologies in SQL
# ---------------------------------------------------------------------------

def _two_site_estate(db_session, pid, hosts_per_site=3):
    scope = Scope(project_id=pid, name="scope")
    db_session.add(scope)
    sites = [Site(project_id=pid, name="HQ", criticality_tier=1),
             Site(project_id=pid, name="Branch", criticality_tier=3)]
    db_session.add_all(sites)
    db_session.flush()
    subnets = [Subnet(scope_id=scope.id, cidr="10.1.1.0/24", site="HQ", site_id=sites[0].id),
               Subnet(scope_id=scope.id, cidr="10.2.2.0/24", site="Branch", site_id=sites[1].id)]
    db_session.add_all(subnets)
    db_session.flush()
    hosts = []
    for index, subnet in enumerate(subnets, start=1):
        for n in range(1, hosts_per_site + 1):
            host = _host(db_session, pid, f"10.{index}.{index}.{n}")
            db_session.add(HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
            hosts.append(host)
    db_session.flush()
    return hosts


def test_plugin_monoculture_is_counted_in_sql_and_reads_titles_only_for_survivors(db_session, test_project):
    pid = test_project.id
    hosts = _two_site_estate(db_session, pid)
    scan = _scan(db_session, pid, "nessus")
    outside = _host(db_session, pid, "192.168.50.1")  # in the project, in no subnet

    def vuln(host, plugin, title, severity=VulnerabilitySeverity.HIGH):
        db_session.add(Vulnerability(host_id=host.id, scan_id=scan.id, plugin_id=plugin, title=title,
                                     severity=severity, source=VulnerabilitySource.NESSUS))

    for host in hosts:                       # every in-scope host, both sites
        vuln(host, "1001", "Shared SSL problem")
        vuln(host, "9001", "Service detection", VulnerabilitySeverity.INFO)  # info: never systemic
    vuln(hosts[0], "1001", "Shared SSL problem")   # a second row on one host is still one host
    for host in hosts[:3]:                   # one site only: systemic count, not estate-wide
        vuln(host, "2002", "One-site problem")
    vuln(hosts[0], "3003", "A one-off")      # below the host floor
    # Reaches the SQL floor only by counting a host OUTSIDE the estate; the
    # in-scope recount must drop it.
    vuln(hosts[0], "4004", "Mostly elsewhere")
    vuln(outside, "4004", "Mostly elsewhere")
    vuln(hosts[0], None, "No plugin id")
    db_session.flush()

    with statements(db_session) as seen:
        out = compute_systemic_insights(db_session, pid)

    shared = {row["key"]: row for row in out["blind_spots"] if row["key"].startswith("vuln:")}
    assert set(shared) == {"vuln:1001"}
    row = shared["vuln:1001"]
    assert row["label"] == "Shared vulnerability: Shared SSL problem"
    assert (row["affected_hosts"], row["subnet_spread"], row["site_spread"]) == (6, 2, 2)
    assert row["host_fraction"] == 1.0 and row["severity"] == "high"
    assert row["systemic_score"] == round(8 * 6 * (1 + 2 + 2), 1)
    assert len(row["example_ips"]) == 5 and all(ip.startswith("10.") for ip in row["example_ips"])

    # No statement reads a title for every vulnerability row of the project:
    # the one that selects titles is restricted to chosen row ids.
    titled = [s for s in seen if "vulnerabilities.title" in s.split("FROM")[0]]
    assert len(titled) == 1 and "vulnerabilities.id IN" in titled[0], titled
    grouped = [s for s in seen if "GROUP BY vulnerabilities.plugin_id" in s and "HAVING" in s]
    assert len(grouped) == 1, [s for s in seen if "plugin_id" in s]


def test_small_estate_runs_no_plugin_query_at_all(db_session, test_project):
    pid = test_project.id
    hosts = _two_site_estate(db_session, pid, hosts_per_site=1)  # 2 hosts: below the estate floor
    scan = _scan(db_session, pid, "nessus")
    for host in hosts:
        db_session.add(Vulnerability(host_id=host.id, scan_id=scan.id, plugin_id="1001", title="t",
                                     severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS))
    db_session.flush()
    with statements(db_session) as seen:
        out = compute_systemic_insights(db_session, pid)
    assert not [r for r in out["blind_spots"] if r["key"].startswith("vuln:")]
    assert not [s for s in seen if "vulnerabilities.plugin_id" in s]


def test_coverage_no_longer_reads_or_returns_technologies(client, db_session, test_project):
    """R22 grouped the coverage read's "top technologies" in SQL; nothing has
    read the field since the Scope page dropped "Technologies observed", so
    the read no longer computes or returns it (owner decision 2026-10-10).
    The technologies a project runs are the Hosts filter's facet."""
    pid = test_project.id
    scan = _scan(db_session, pid, "httpx")
    host = _host(db_session, pid, "10.22.0.1")
    db_session.add(models.WebInterface(
        project_id=pid, host_id=host.id, scan_id=scan.id, port=80,
        url="http://10.22.0.1/", technologies=["Nginx", "React"],
    ))
    db_session.flush()

    with statements(db_session) as seen:
        response = client.get(f"/api/v1/projects/{pid}/scopes/coverage")
    assert response.status_code == 200, response.text
    assert "top_technologies" not in response.json()
    assert not [s for s in seen if "web_interfaces" in s and "technologies" in s]
    assert not hasattr(scopes_endpoint, "_technology_host_counts")


# ---------------------------------------------------------------------------
# R24 — many subnets, one pass over the hosts
# ---------------------------------------------------------------------------

def _mappings(db_session, subnet_ids):
    return {
        (m.host_id, m.subnet_id) for m in
        db_session.query(HostSubnetMapping).filter(HostSubnetMapping.subnet_id.in_(subnet_ids)).all()
    }


def test_correlate_subnets_reads_the_hosts_once_and_matches_the_one_by_one_result(db_session, test_project):
    pid = test_project.id
    other = _project(db_session, "r24-other")
    scope = Scope(project_id=pid, name="s")
    db_session.add(scope)
    db_session.flush()
    cidrs = ["10.24.0.0/24", "10.24.1.0/24", "10.24.0.0/16", "10.24.0.8/29", "172.16.0.0/12", "2001:db8::/64"]
    subnets = [Subnet(scope_id=scope.id, cidr=cidr) for cidr in cidrs]
    db_session.add_all(subnets)
    hosts = [_host(db_session, pid, ip) for ip in
             ("10.24.0.9", "10.24.0.200", "10.24.1.5", "10.24.77.1", "192.168.1.1", "2001:db8::5")]
    foreign = _host(db_session, other.id, "10.24.0.9")  # another project's host at the same address
    db_session.flush()
    ids = [s.id for s in subnets]
    service = SubnetCorrelationService(db_session)

    for subnet_id in ids:                      # the old way: one pass per subnet
        service.correlate_subnet(subnet_id)
    one_by_one = _mappings(db_session, ids)
    assert one_by_one and foreign.id not in {host_id for host_id, _ in one_by_one}
    by_ip = {h.id: h.ip_address for h in hosts}
    assert {(by_ip[h], next(s.cidr for s in subnets if s.id == sid)) for h, sid in one_by_one} == {
        ("10.24.0.9", "10.24.0.0/24"), ("10.24.0.9", "10.24.0.0/16"), ("10.24.0.9", "10.24.0.8/29"),
        ("10.24.0.200", "10.24.0.0/24"), ("10.24.0.200", "10.24.0.0/16"),
        ("10.24.1.5", "10.24.1.0/24"), ("10.24.1.5", "10.24.0.0/16"),
        ("10.24.77.1", "10.24.0.0/16"), ("2001:db8::5", "2001:db8::/64"),
    }

    db_session.query(HostSubnetMapping).filter(HostSubnetMapping.subnet_id.in_(ids)).delete(
        synchronize_session=False)
    # A stale mapping the pass must replace, and a mapping of ANOTHER subnet it must leave.
    untouched_subnet = Subnet(scope_id=scope.id, cidr="192.168.1.0/24")
    db_session.add(untouched_subnet)
    db_session.flush()
    db_session.add(HostSubnetMapping(host_id=hosts[4].id, subnet_id=untouched_subnet.id))
    db_session.add(HostSubnetMapping(host_id=hosts[4].id, subnet_id=ids[0]))  # 192.168.1.1 is not in it
    db_session.flush()

    with statements(db_session) as seen:
        written = service.correlate_subnets(ids + [ids[0], None, 10 ** 9])
    assert _mappings(db_session, ids) == one_by_one
    assert written == len(one_by_one)
    assert _mappings(db_session, [untouched_subnet.id]) == {(hosts[4].id, untouched_subnet.id)}
    host_reads = [s for s in seen if s.lstrip().upper().startswith("SELECT") and "FROM hosts_v2" in s]
    assert len(host_reads) == 1, host_reads
    assert len([s for s in seen if s.lstrip().upper().startswith("DELETE")]) == 1

    assert service.correlate_subnets([]) == 0
    assert service.correlate_subnets([10 ** 9]) == 0


def test_adding_subnets_reads_the_hosts_once_however_many_are_added(client, db_session, test_project):
    pid = test_project.id
    scope = Scope(project_id=pid, name="s")
    db_session.add(scope)
    for n in range(1, 9):
        _host(db_session, pid, f"10.24.{n}.10")
    db_session.flush()

    body = {"subnets": [{"cidr": f"10.24.{n}.0/24", "description": f"net {n}"} for n in range(1, 9)]}
    with statements(db_session) as seen:
        response = client.post(f"/api/v1/projects/{pid}/scopes/{scope.id}/subnets", json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert [row["cidr"] for row in created] == [item["cidr"] for item in body["subnets"]]
    assert all(row["id"] for row in created)

    host_reads = [s for s in seen if "hosts_v2.ip_address" in s.split("FROM")[0]]
    assert len(host_reads) == 1, f"{len(host_reads)} host reads for {len(created)} subnets"

    mapped = _mappings(db_session, [row["id"] for row in created])
    assert len(mapped) == 8 and len({subnet_id for _, subnet_id in mapped}) == 8


# ---------------------------------------------------------------------------
# R18 — /hosts/filters/data: cache, one evaluation of the filter
# ---------------------------------------------------------------------------

@pytest.fixture
def facet_cache():
    hosts_endpoint._FACET_CACHE.clear()
    yield hosts_endpoint._FACET_CACHE
    hosts_endpoint._FACET_CACHE.clear()


@pytest.fixture
def facet_estate(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid, "nessus")
    scope = Scope(project_id=pid, name="s")
    db_session.add(scope)
    db_session.flush()
    subnet = Subnet(scope_id=scope.id, cidr="10.18.0.0/24", site="HQ")
    db_session.add(subnet)
    db_session.flush()
    hosts = []
    for n in range(1, 7):
        host = _host(db_session, pid, f"10.18.0.{n}", hostname=f"web-{n}" if n % 2 else f"db-{n}",
                     os_name="Linux" if n % 2 else "Windows Server 2008")
        _port(db_session, host, 22, service_name="ssh", service_product="OpenSSH", service_version="7.4")
        _port(db_session, host, 80 if n % 2 else 1433, "open" if n < 5 else "closed",
              service_name="http" if n % 2 else "ms-sql-s")
        db_session.add(HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
        db_session.add(models.WebInterface(project_id=pid, host_id=host.id, scan_id=scan.id, port=80,
                                           url=f"http://10.18.0.{n}/", technologies=["Nginx", f"Lib{n % 2}"]))
        db_session.add(Vulnerability(host_id=host.id, scan_id=scan.id, title="t", plugin_id="1",
                                     severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS,
                                     check_id="smb_signing_not_required" if n % 3 == 0 else None))
        hosts.append(host)
    db_session.flush()
    return pid, hosts


def _facets(client, pid, **params):
    response = client.get(f"/api/v1/projects/{pid}/hosts/filters/data", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_unfiltered_facets_are_served_from_the_cache_and_edits_still_show(
    client, db_session, facet_estate, facet_cache,
):
    pid, hosts = facet_estate
    with statements(db_session) as first:
        cold = _facets(client, pid)
    with statements(db_session) as second:
        warm = _facets(client, pid)
    assert warm == cold
    assert cold["common_ports"] and cold["services"] and cold["technologies"] and cold["weaknesses"]
    # ports + labels + services + OS + technologies + 3 RDAP + weaknesses + checks
    assert len(first) - len(second) >= 10, (len(first), len(second))
    assert not [s for s in second if "ports_v2" in s or "vulnerabilities" in s]

    # What a person edits is never cached: a tag made now is offered now.
    db_session.add(models.HostTag(project_id=pid, name="fresh-tag", color="#fff"))
    db_session.flush()
    assert "fresh-tag" in {t["name"] for t in _facets(client, pid)["tags"]}

    # Scan-derived counts wait for the TTL …
    _port(db_session, hosts[0], 3389, service_name="ms-wbt-server")
    assert 3389 not in {p["port"] for p in _facets(client, pid)["common_ports"]}
    # … and a FILTERED request never reads the cache.
    assert 3389 in {p["port"] for p in _facets(client, pid, state="up")["common_ports"]}
    facet_cache.clear()
    assert 3389 in {p["port"] for p in _facets(client, pid)["common_ports"]}


def test_facet_cache_is_per_project_and_can_be_turned_off(
    client, db_session, facet_estate, facet_cache, monkeypatch,
):
    pid, _hosts = facet_estate
    other = _project(db_session, "r18-other")
    lonely = _host(db_session, other.id, "10.18.9.1")
    _port(db_session, lonely, 5900, service_name="vnc")
    db_session.flush()

    mine = _facets(client, pid)
    theirs = _facets(client, other.id)
    assert {p["port"] for p in theirs["common_ports"]} == {5900}
    assert 5900 not in {p["port"] for p in mine["common_ports"]}
    assert set(facet_cache) == {pid, other.id}
    assert _facets(client, other.id) == theirs and _facets(client, pid) == mine

    facet_cache.clear()
    monkeypatch.setattr(hosts_endpoint.settings, "HOST_FACET_CACHE_SECONDS", 0, raising=False)
    with statements(db_session) as a:
        _facets(client, pid)
    with statements(db_session) as b:
        _facets(client, pid)
    assert not facet_cache and len(a) == len(b)


@pytest.mark.parametrize("params", [
    dict(search="web"), dict(state="up", os_filter="Linux"), dict(services="ssh"),
    dict(ports="80", port_states="closed"), dict(q="has:open_ports AND os:linux"),
    dict(subnets="10.18.0.0/24"), dict(sites="HQ", search="db"),
    dict(weaknesses="eol"), dict(checks="smb_signing_not_required", search="1"),
    dict(search="matches-nothing-at-all"),
])
def test_filtered_facets_equal_the_embedded_subquery_form(
    client, db_session, facet_estate, facet_cache, monkeypatch, params,
):
    """The id-list path (Postgres) against the subquery path it replaced."""
    if not _is_postgres(db_session):
        pytest.skip("the id-array path is Postgres-only")
    pid, _hosts = facet_estate
    with_ids = _facets(client, pid, **params)

    original = hosts_endpoint._FacetScope.__init__

    def without_ids(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.ids = None  # the SQLite branch: the filter embedded in every statement

    monkeypatch.setattr(hosts_endpoint._FacetScope, "__init__", without_ids)
    assert _facets(client, pid, **params) == with_ids
    assert not facet_cache  # neither filtered request touched the cache


def test_the_filter_is_evaluated_once_per_filtered_facet_request(
    client, db_session, facet_estate, facet_cache, monkeypatch,
):
    if not _is_postgres(db_session):
        pytest.skip("the id-array path is Postgres-only")
    pid, _hosts = facet_estate

    def evaluations(seen):
        # The search term reaches Postgres as an ILIKE on the host name.
        return len([s for s in seen if "hosts_v2.hostname ILIKE" in s])

    with statements(db_session) as new:
        _facets(client, pid, search="web")
    assert evaluations(new) == 1

    original = hosts_endpoint._FacetScope.__init__

    def without_ids(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.ids = None

    monkeypatch.setattr(hosts_endpoint._FacetScope, "__init__", without_ids)
    with statements(db_session) as old:
        _facets(client, pid, search="web")
    # Before: once in every facet statement.  Now: the one id fetch, which is
    # the single statement the request gained.
    assert evaluations(old) >= 10
    assert len(new) <= len(old) + 1


# ---------------------------------------------------------------------------
# Review 2026-10-08 — the remaining ``Host.id IN (subquery)`` predicates as
# correlated EXISTS
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_WIDE_HOSTS = 9


@pytest.fixture
def wide_estate(db_session, test_project, test_user):
    """Nine hosts per project, each carrying one kind of row a converted
    predicate reads; host 9 carries nothing and has no OS, name or SMB posture
    (NULLs).  The same rows exist in a second project, and a second person
    holds one review."""
    other_user = User(
        id=test_user.id + 100,   # the fixture's user took id 1 without the sequence
        username="wide-other", email="wide-other@example.com", full_name="Other",
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
    )
    db_session.add(other_user)
    db_session.flush()
    other_project = _project(db_session, "wide-other")
    facts = {}
    for index, pid in enumerate((test_project.id, other_project.id)):
        old_scan = _scan(db_session, pid)
        new_scan = _scan(db_session, pid, tool="nessus")
        h = [None] + [
            _host(db_session, pid, f"10.8.{index}.{n}", first_seen=_T0 - timedelta(days=30))
            for n in range(1, _WIDE_HOSTS + 1)
        ]
        h[9].first_seen = _T0 + timedelta(days=5)
        h[1].smb_signing, h[2].smb_signing = "disabled", "required"

        smb = _port(db_session, h[1], 445, first_seen=_T0 + timedelta(days=10))
        web = _port(db_session, h[4], 80, first_seen=_T0 - timedelta(days=30))

        def vuln(host, severity, title, **extra):
            db_session.add(Vulnerability(
                host_id=host.id, scan_id=new_scan.id, plugin_id=f"w-{host.id}-{title}", title=title,
                severity=severity, source=VulnerabilitySource.NESSUS, **extra,
            ))

        vuln(h[1], VulnerabilitySeverity.CRITICAL, "Log4Shell RCE", cve_id="CVE-2021-44228",
             issue_key="cve:CVE-2021-44228", check_id="smb_signing_not_required", exploitable=True,
             port_id=smb.id, created_at=_T0 + timedelta(days=10))
        vuln(h[2], VulnerabilitySeverity.HIGH, "Weak cipher", created_at=_T0 - timedelta(days=10))
        vuln(h[2], VulnerabilitySeverity.INFO, "Service detection", created_at=_T0 - timedelta(days=10))

        db_session.add(Annotation(host_id=h[3].id, user_id=test_user.id, body="rotate creds"))
        db_session.add(Annotation(host_id=None, port_id=web.id, user_id=test_user.id, body="odd banner"))

        db_session.add(NetexecResult(scan_id=new_scan.id, host_id=h[5].id, protocol="smb", local_admin=True))
        db_session.add(NetexecResult(scan_id=new_scan.id, host_id=h[6].id, protocol="smb",
                                     local_admin=False, writable_share=True))

        db_session.add(HostFollow(host_id=h[6].id, user_id=test_user.id, status=models.FollowStatus.IN_REVIEW,
                                  assigned_at=_T0))
        db_session.add(HostFollow(host_id=h[7].id, user_id=test_user.id, status=models.FollowStatus.REVIEWED,
                                  review_conclusion="needs_evidence", reviewed_at=_T0))
        db_session.add(HostFollow(host_id=h[8].id, user_id=other_user.id, status=models.FollowStatus.IN_REVIEW,
                                  assigned_at=_T0))
        db_session.add(HostFollow(host_id=h[2].id, user_id=test_user.id, status=models.FollowStatus.WATCHING))

        for host, scan, when in ((h[1], old_scan, -30), (h[2], old_scan, -30), (h[2], new_scan, 1), (h[3], new_scan, 1)):
            db_session.add(models.HostScanHistory(
                host_id=host.id, scan_id=scan.id, discovered_at=_T0 + timedelta(days=when)))

        acme = NetworkAttribution(project_id=pid, cidr="10.8.0.0/25", asn=64500, org_name="Acme Corp",
                                  country="US", source="rdap")
        cloud = NetworkAttribution(project_id=pid, cidr="10.8.0.128/25", asn=64501, org_name="Hoster BV",
                                   country="NL", cloud_provider="aws", source="rdap")
        db_session.add_all([acme, cloud])
        db_session.flush()
        db_session.add(HostNetworkAttribution(host_id=h[1].id, attribution_id=acme.id))
        db_session.add(HostNetworkAttribution(host_id=h[2].id, attribution_id=cloud.id))

        scope = Scope(project_id=pid, name="wide")
        tag = models.HostTag(project_id=pid, name="Crown")
        label = models.SubnetLabel(project_id=pid, name="DMZ")
        db_session.add_all([scope, tag, label])
        db_session.flush()
        sited = Subnet(scope_id=scope.id, cidr="10.8.4.0/24", site="HQ")
        bare = Subnet(scope_id=scope.id, cidr="10.8.5.0/24")
        db_session.add_all([sited, bare])
        db_session.flush()
        db_session.add(models.HostTagAssignment(host_id=h[3].id, tag_id=tag.id))
        db_session.add(models.SubnetLabelAssignment(subnet_id=sited.id, label_id=label.id))
        db_session.add(HostSubnetMapping(host_id=h[4].id, subnet_id=sited.id))
        db_session.add(HostSubnetMapping(host_id=h[5].id, subnet_id=bare.id))

        db_session.add(models.WebInterface(
            scan_id=new_scan.id, project_id=pid, host_id=h[6].id, source="httpx",
            url=f"https://10.8.{index}.6/", cert_subject_org="Acme Corp"))

        db_session.add(HostTest(
            project_id=pid, host_id=h[7].id, tool="nmap", description="d", rationale="r",
            priority="high", status="proposed", source="person",
            request_key=f"wide-{pid}", request_hash="0" * 64))
        db_session.add(EvidenceRecord(project_id=pid, host_id=h[8].id, tool="nmap", outcome="finding", summary="s"))
        db_session.flush()
        # The key the row was stored under (the model derives it on flush).
        issue = db_session.query(Vulnerability.issue_key).filter(
            Vulnerability.host_id == h[1].id).scalar()
        facts[pid] = dict(new_scan=new_scan.id, label=label.id, issue=issue)
    db_session.flush()
    return test_project.id, other_project.id, other_user, facts


def _old_vuln_in(db, pid, *conditions, joins=()):
    """The scanner-row predicates as they stood: ``Host.id IN`` a subquery
    joined to an aliased Host to keep it inside the project."""
    H = aliased(models.Host)
    sub = db.query(Vulnerability.host_id).join(H, H.id == Vulnerability.host_id)
    for target, on in joins:
        sub = sub.join(target, on)
    return models.Host.id.in_(sub.filter(H.project_id == pid, *conditions).distinct())


def _old_follow_in(db, *conditions):
    return models.Host.id.in_(db.query(HostFollow.host_id).filter(*conditions))


def _old_attributed_in(db, *conditions):
    sub = (
        db.query(HostNetworkAttribution.host_id)
        .join(NetworkAttribution, NetworkAttribution.id == HostNetworkAttribution.attribution_id)
        .filter(*conditions).distinct()
    )
    return models.Host.id.in_(sub)


def _old_labelled_in(db, pid, *conditions):
    sub = (
        db.query(HostSubnetMapping.host_id)
        .join(models.SubnetLabelAssignment, models.SubnetLabelAssignment.subnet_id == HostSubnetMapping.subnet_id)
        .join(models.SubnetLabel, models.SubnetLabel.id == models.SubnetLabelAssignment.label_id)
        .filter(models.SubnetLabel.project_id == pid, *conditions).distinct()
    )
    return models.Host.id.in_(sub)


def _wide_pairs(db, pid, user, facts):
    """name → (the predicate now, the predicate as it stood)."""
    V = Vulnerability
    H1, H2, H3, H4 = (aliased(models.Host) for _ in range(4))
    earlier = aliased(models.HostScanHistory)
    in_scan = db.query(models.HostScanHistory.host_id).filter(
        models.HostScanHistory.scan_id.in_([facts["new_scan"]]))
    mapped = (
        db.query(HostSubnetMapping.host_id)
        .join(Subnet, Subnet.id == HostSubnetMapping.subnet_id)
        .join(Scope, Scope.id == Subnet.scope_id)
        .filter(Scope.project_id == pid)
    )
    assigned = db.query(HostFollow.host_id).filter(HostFollow.assigned_at.isnot(None))
    critical_window = (
        (V.created_at > _T0) & func.lower(cast(V.severity, String)).in_(["critical"])
    )
    return {
        "issue": (P.issue_predicate([facts["issue"]]),
                  _old_vuln_in(db, pid, V.issue_key.in_([facts["issue"]]))),
        "cve": (P.cve_predicate(["cve-2021"]), _old_vuln_in(db, pid, V.cve_id.ilike("%cve-2021%"))),
        "vuln": (P.vuln_predicate(["log4"]), _old_vuln_in(db, pid, V.title.ilike("%log4%"))),
        "severity": (P.severity_predicate(["critical", "high"]),
                     _old_vuln_in(db, pid, V.severity.in_(["CRITICAL", "HIGH"]))),
        "kind misconfiguration": (P.kind_predicate(["misconfiguration"]),
                                  _old_vuln_in(db, pid, V.check_id.isnot(None))),
        "kind informational": (P.kind_predicate(["informational"]),
                               _old_vuln_in(db, pid, V.check_id.is_(None), V.severity == "INFO")),
        "check": (P.check_predicate(["smb_signing_not_required"]),
                  _old_vuln_in(db, pid, V.check_id.in_(["smb_signing_not_required"]))),
        "exploit": (P.has_exploit_predicate(), _old_vuln_in(db, pid, V.exploitable.is_(True))),
        "critical exploit": (
            P.critical_exploit_predicate(),
            _old_vuln_in(db, pid, V.exploitable.is_(True), V.severity == VulnerabilitySeverity.CRITICAL)),
        "exploitport": (
            P.exploit_on_port_predicate([445]),
            _old_vuln_in(db, pid, V.exploitable.is_(True), models.Port.port_number.in_([445]),
                         joins=[(models.Port, models.Port.id == V.port_id)])),
        "vuln window": (P.vuln_window_predicate(_T0, None, ["critical"]),
                        _old_vuln_in(db, pid, critical_window)),
        "changed window": (
            P.changed_window_predicate(_T0),
            (models.Host.first_seen <= _T0) & (
                models.Host.id.in_(db.query(models.Port.host_id).filter(models.Port.first_seen > _T0).distinct())
                | _old_vuln_in(db, pid, V.created_at > _T0))),
        "has notes": (
            P.has_notes_predicate(),
            models.Host.id.in_(
                db.query(Annotation.host_id).join(H1, H1.id == Annotation.host_id)
                .filter(H1.project_id == pid, Annotation.host_id.isnot(None))
                .union(
                    db.query(models.Port.host_id)
                    .join(Annotation, Annotation.port_id == models.Port.id)
                    .join(H2, H2.id == models.Port.host_id).filter(H2.project_id == pid)))),
        "note": (
            P.note_predicate(["rotate"]),
            models.Host.id.in_(
                db.query(Annotation.host_id).join(H3, H3.id == Annotation.host_id)
                .filter(H3.project_id == pid, Annotation.body.ilike("%rotate%")).distinct())),
        "tested": (P.has_test_execution_predicate(pid), models.Host.id.in_(tested_host_ids(pid))),
        "planned": (P.has_plan_entry_predicate(pid), models.Host.id.in_(planned_host_ids(pid))),
        "local admin": (
            P.local_admin_predicate(),
            models.Host.id.in_(
                db.query(NetexecResult.host_id).join(H4, NetexecResult.host_id == H4.id)
                .filter(H4.project_id == pid, NetexecResult.local_admin.is_(True)))),
        "writable share": (
            P.writable_share_predicate(),
            models.Host.id.in_(
                db.query(NetexecResult.host_id).filter(NetexecResult.writable_share.is_(True)))),
        "smb unsigned": (
            P.smb_unsigned_predicate(),
            models.Host.id.in_(
                db.query(H1.id).filter(H1.project_id == pid, H1.smb_signing.in_(("disabled", "not_required"))))),
        "tag name": (
            P.tag_predicate_by_name(["CROWN"], pid),
            models.Host.id.in_(
                db.query(models.HostTagAssignment.host_id)
                .join(models.HostTag, models.HostTag.id == models.HostTagAssignment.tag_id)
                .filter(models.HostTag.project_id == pid, func.lower(models.HostTag.name).in_(["crown"]))
                .distinct())),
        "label id": (P.label_predicate_by_id([facts["label"]], pid),
                     _old_labelled_in(db, pid, models.SubnetLabelAssignment.label_id.in_([facts["label"]]))),
        "label name": (P.label_predicate_by_name(["dmz"], pid),
                       _old_labelled_in(db, pid, func.lower(models.SubnetLabel.name).in_(["dmz"]))),
        "site none": (
            P.site_none_predicate(pid),
            models.Host.id.in_(mapped.distinct()) & models.Host.id.notin_(
                mapped.filter(func.trim(func.coalesce(Subnet.site, "")) != "").distinct())),
        "follow in_review": (P.follow_predicate("in_review", user),
                             _old_follow_in(db, HostFollow.status == "in_review")),
        "follow reviewed": (P.follow_predicate("reviewed", user),
                            _old_follow_in(db, HostFollow.status == "reviewed")),
        "follow mine": (P.follow_predicate("mine", user),
                        _old_follow_in(db, HostFollow.user_id == user.id, HostFollow.status == "in_review")),
        "follow watching": (P.follow_predicate("watching", user),
                            _old_follow_in(db, HostFollow.user_id == user.id, HostFollow.status == "watching")),
        # ("conclusion" — `review_conclusion_predicate`, old == new until the
        # `conclusion:` query word was retired with the review conclusions —
        # went with the predicate: there is nothing left to compare.)
        "assigned any": (P.assigned_predicate(db, "any", user), models.Host.id.in_(assigned)),
        "assigned none": (P.assigned_predicate(db, "none", user), ~models.Host.id.in_(assigned)),
        "assigned me": (
            P.assigned_predicate(db, "me", user),
            _old_follow_in(db, HostFollow.user_id == user.id, HostFollow.assigned_at.isnot(None))),
        "assigned by name": (
            P.assigned_predicate(db, "WIDE-OTHER", user),
            _old_follow_in(db, HostFollow.user_id != user.id, HostFollow.assigned_at.isnot(None))),
        "scan": (P.scan_predicate([facts["new_scan"]]), models.Host.id.in_(in_scan)),
        "scan first seen": (
            P.scan_predicate([facts["new_scan"]], first_seen_only=True),
            models.Host.id.in_(in_scan.filter(~exists().where(
                (earlier.host_id == models.HostScanHistory.host_id)
                & (earlier.discovered_at < models.HostScanHistory.discovered_at))))),
        "org": (P.attribution_org_predicate(["acme"]),
                _old_attributed_in(db, NetworkAttribution.org_name.ilike("%acme%"))),
        "asn": (P.attribution_asn_predicate(["AS64500"]),
                _old_attributed_in(db, NetworkAttribution.asn.in_([64500]))),
        "country": (P.attribution_country_predicate(["us"]),
                    _old_attributed_in(db, func.upper(NetworkAttribution.country).in_(["US"]))),
        "cloud": (P.attribution_cloud_predicate(["aws"]),
                  _old_attributed_in(db, func.lower(NetworkAttribution.cloud_provider).in_(["aws"]))),
        "cloud none": (P.attribution_cloud_predicate(["none"]),
                       _old_attributed_in(db, NetworkAttribution.cloud_provider.is_(None))),
        "certorg": (
            P.cert_org_predicate(["acme"]),
            models.Host.id.in_(
                db.query(models.WebInterface.host_id)
                .filter(models.WebInterface.cert_subject_org.ilike("%acme%")).distinct())),
    }


_WIDE_NAMES = [
    "issue", "cve", "vuln", "severity", "kind misconfiguration", "kind informational", "check",
    "exploit", "critical exploit", "exploitport", "vuln window", "changed window", "has notes",
    "note", "tested", "planned", "local admin", "writable share", "smb unsigned", "tag name",
    "label id", "label name", "site none", "follow in_review", "follow reviewed", "follow mine",
    "follow watching", "assigned any", "assigned none", "assigned me",
    "assigned by name", "scan", "scan first seen", "org", "asn", "country", "cloud", "cloud none",
    "certorg",
]


@pytest.mark.parametrize("name", _WIDE_NAMES)
def test_converted_predicates_select_what_the_in_form_selected(db_session, test_user, wide_estate, name):
    pid, other_id, _other_user, facts = wide_estate
    for project_id in (pid, other_id):
        pairs = _wide_pairs(db_session, project_id, test_user, facts[project_id])
        assert set(pairs) == set(_WIDE_NAMES)
        new, old = pairs[name]
        selected = _ids(db_session, project_id, new)
        assert selected == _ids(db_session, project_id, old), name
        assert 0 < len(selected) < _WIDE_HOSTS, name     # the fixture really tells hosts apart
        # … and negated the way the DSL negates (an unknown child reads as
        # false), where NOT IN and NOT EXISTS would part ways.
        negated = _ids(db_session, project_id, negate(new))
        assert negated == _ids(db_session, project_id, ~old), name
        assert len(negated) == _WIDE_HOSTS - len(selected), name


@pytest.mark.parametrize("q", [
    "NOT has:critical", "NOT cve:CVE-2021", "has:notes OR has:exploit", "NOT has:tested",
    "NOT has:planned", "NOT has:local_admin", "follow:in_review OR follow:reviewed", "NOT follow:mine",
    "assigned:none", "NOT assigned:any", "NOT follow:revisit", "NOT org:acme",
    "NOT country:US", "NOT asn:64500", "NOT certorg:acme", "NOT tag:crown", "NOT label:dmz",
    "site:none", 'NOT issue:"cve:CVE-2021-44228"', "NOT kind:misconfiguration",
    "NOT check:smb_signing_not_required", "NOT exploitport:445", "NOT note:rotate",
    'changedsince:"2026-01-01T00:00:00Z"', 'NOT vulnsince:"critical@2026-01-01T00:00:00Z"',
])
def test_converted_filters_never_reach_postgres_as_in_or_not_in(db_session, test_user, wide_estate, q):
    pid, _other_id, _other_user, facts = wide_estate
    query = build_filtered_host_query(db_session, test_user, project_id=pid, q=q).with_entities(models.Host.id)
    sql = str(query.statement.compile(dialect=db_session.get_bind().dialect))
    assert "EXISTS (SELECT" in sql, sql
    assert " IN (SELECT" not in sql, sql
    # Reached through the outer query's hosts: no subquery re-joins hosts.
    assert sql.count("FROM hosts_v2") == 1 and "JOIN hosts_v2" not in sql, sql
    query.all()


def test_discrete_filters_reach_postgres_as_exists_too(db_session, test_user, wide_estate):
    pid, _other_id, _other_user, facts = wide_estate
    query = build_filtered_host_query(
        db_session, test_user, project_id=pid,
        has_critical_vulns=True, has_exploit_available=True, has_test_execution=True,
        with_notes_only=True, follow_status="in_review", assigned_to="any",
        scan_ids=str(facts[pid]["new_scan"]), first_seen_in_scan=True,
        subnet_labels=str(facts[pid]["label"]), orgs=["acme"], asns=["64500"], countries=["US"],
        checks="smb_signing_not_required", out_of_scope_only=True,
    ).with_entities(models.Host.id)
    sql = str(query.statement.compile(dialect=db_session.get_bind().dialect))
    assert " IN (SELECT" not in sql, sql
    assert "JOIN hosts_v2" not in sql and "LEFT OUTER JOIN" not in sql, sql
    query.all()


#: The four flags that resolve an id set in Python (latest observation wins)
#: and pass it as a literal list — not a subquery.
_ID_SET_PREDICATES = {"eol_os_predicate", "cert_issue_predicate", "weak_auth_predicate", "weak_tls_predicate"}


def test_no_host_predicate_is_an_in_subquery_any_more():
    """A host predicate over a child table is a correlated EXISTS.  A new
    ``Host.id.in_(…)`` in the predicate module is a predicate that runs its
    subquery over every project's rows under OR or NOT — write it with
    ``_host_has`` / ``host_has_port``."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(P))
    offenders = []
    for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        source = ast.unparse(function)
        if function.name in _ID_SET_PREDICATES:
            continue
        if "Host.id.in_(" in source or "Host.id.notin_(" in source or "Host.id.not_in(" in source:
            offenders.append(function.name)
    assert offenders == []
    for name in _ID_SET_PREDICATES:
        assert "Host.id.in_(ids)" in inspect.getsource(getattr(P, name)), name


# ---------------------------------------------------------------------------
# N9 / R20 — the revision and the models declare the same indexes
# ---------------------------------------------------------------------------

def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "c3f6b9d2e5a7_read_path_indexes.py"
    spec = importlib.util.spec_from_file_location("read_path_indexes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_index_the_revision_creates_is_declared_on_its_model():
    migration = _load_migration()
    assert migration.revision == "c3f6b9d2e5a7" and migration.down_revision == "b2e5a8c1d4f6"
    metadata = models.Base.metadata
    for name, table, columns in migration._BTREE_INDEXES:
        declared = {index.name: [c.name for c in index.columns] for index in metadata.tables[table].indexes}
        assert declared.get(name) == columns, (name, declared.get(name))
    # The trigram indexes are migration-only, under the prefix alembic ignores.
    for name, _table, _column in migration._TRGM_INDEXES:
        assert name.startswith("ix_trgm_")
    assert migration._PRODUCT_VERSION_INDEX.startswith("ix_trgm_")
