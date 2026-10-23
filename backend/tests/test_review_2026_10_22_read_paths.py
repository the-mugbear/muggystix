"""Review 2026-10-22 — read-path items A-R7, A-R8, A-R9 and the evidence-gap
nitpick.

* **A-R7** — ``GET /dashboard/stats`` answers only what the Posture page reads
  (the scanner observation counts), in ONE statement: the per-source grouping
  nobody returned and the third distinct-host pass are gone.
* **A-R8** — "currently resolves to" (``current_binding_condition``) reads the
  name's newest answer with ONE probe, served by a partial expression index.
  The two-subquery form it replaced is kept below as the reference.
* **A-R9** — the names export reads the list's query once (a server-side
  cursor), never OFFSET pages; the files are the list, in its order.
* **Nitpick** — ``evidence_gap_hosts`` cuts the WHOLE gap by address in SQL
  (``inet`` order, IPv6 included), not the first N by id.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, distinct, event, func, or_, select, text, update
from sqlalchemy.orm import aliased

from app.api.v1.endpoints import dns_names as dns_names_routes
from app.services import names_list_service
from app.db import models
from app.db.models import DNS_RESOLVING_TYPES
from app.db.models_project import Project
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services import dns_name_service as svc
from app.services.evidence_service import evidence_gap_hosts
from app.services.vulnerability_service import VulnerabilityService


def _statements(db_session):
    """Collect the SQL the test connection runs while the block is open."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db_session.get_bind().engine
    return seen, engine, record


def _postgres(db_session):
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("Postgres-only")


# --- A-R7: the dashboard's scanner observation counts ---------------------------

def _vuln(db, host, scan, plugin, severity, source=VulnerabilitySource.NESSUS):
    db.add(Vulnerability(host_id=host.id, scan_id=scan.id, source=source, plugin_id=plugin,
                         title=f"t{plugin}", severity=severity))


def test_the_dashboard_counts_are_one_statement_with_the_same_figures(db_session, test_project):
    pid = test_project.id
    other = Project(name="other-r7", slug="other-r7", description="")
    db_session.add(other)
    db_session.flush()
    scan = models.Scan(filename="n", tool_name="nessus", project_id=pid)
    other_scan = models.Scan(filename="o", tool_name="nessus", project_id=other.id)
    db_session.add_all([scan, other_scan])
    db_session.flush()
    a, b, c = (models.Host(project_id=pid, ip_address=f"10.77.0.{i}", state="up") for i in (1, 2, 3))
    stranger = models.Host(project_id=other.id, ip_address="10.77.0.1", state="up")
    db_session.add_all([a, b, c, stranger])
    db_session.flush()
    # a: critical + high (one host, counted once among the hosts carrying any);
    # b: two high + info from another source; c: nothing; another project's
    # host: a critical that must not count.
    _vuln(db_session, a, scan, "1", VulnerabilitySeverity.CRITICAL)
    _vuln(db_session, a, scan, "2", VulnerabilitySeverity.HIGH)
    _vuln(db_session, b, scan, "3", VulnerabilitySeverity.HIGH)
    _vuln(db_session, b, scan, "4", VulnerabilitySeverity.HIGH)
    _vuln(db_session, b, scan, "5", VulnerabilitySeverity.INFO, source=VulnerabilitySource.OPENVAS)
    _vuln(db_session, stranger, other_scan, "6", VulnerabilitySeverity.CRITICAL)
    db_session.flush()

    seen, engine, record = _statements(db_session)
    event.listen(engine, "before_cursor_execute", record)
    try:
        stats = VulnerabilityService(db_session).get_dashboard_statistics(project_id=pid)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(seen) == 1, seen  # three statements before

    # The reference: each figure counted on its own, as the three passes did.
    in_project = and_(Vulnerability.host_id == models.Host.id, models.Host.project_id == pid)
    by_sev = {
        sev.value: (n, hosts) for sev, n, hosts in db_session.execute(
            select(Vulnerability.severity, func.count(Vulnerability.id),
                   func.count(distinct(Vulnerability.host_id)))
            .where(in_project).group_by(Vulnerability.severity)
        )
    }
    carrying = db_session.execute(
        select(func.count(distinct(Vulnerability.host_id))).where(in_project)
    ).scalar()
    assert carrying == 2
    assert stats["hosts_with_vulnerabilities"] == carrying
    assert stats["total_vulnerabilities"] == 5
    for sev in VulnerabilitySeverity:
        n, hosts = by_sev.get(sev.value, (0, 0))
        assert stats["severity_breakdown"][sev.value] == n
        assert stats["hosts_by_severity"][sev.value] == hosts
    assert "source_breakdown" not in stats


def test_the_dashboard_route_answers_only_the_counts(client, db_session, test_project):
    body = client.get(f"/api/v1/projects/{test_project.id}/dashboard/stats").json()
    assert set(body) == {"vulnerability_stats"}


# --- A-R8: "currently resolves to", one probe --------------------------------------

def _reference_current_binding(r):
    """The two-scalar-subquery form ``current_binding_condition`` had before
    review 2026-10-22 (A-R8) — kept verbatim as the reference."""
    r2 = aliased(models.DNSRecord)
    ts = func.coalesce(r2.observed_at, r2.created_at)
    latest = (
        select(r2.scan_id, ts.label("ts"))
        .where(r2.name_id == r.name_id, r2.record_type.in_(DNS_RESOLVING_TYPES))
        .order_by(ts.desc(), r2.id.desc())
        .limit(1)
        .correlate(r)
    )
    latest_scan = latest.with_only_columns(r2.scan_id).scalar_subquery()
    latest_ts = latest.with_only_columns(ts).scalar_subquery()
    return and_(
        r.record_type.in_(DNS_RESOLVING_TYPES),
        or_(
            and_(r.scan_id.isnot(None), r.scan_id == latest_scan),
            and_(latest_scan.is_(None), func.coalesce(r.observed_at, r.created_at) == latest_ts),
        ),
    )


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _binding_estate(db, project_id):
    """Every case the rule distinguishes.  Returns the values that are
    current, by the rule as documented."""
    scans = [models.Scan(project_id=project_id, filename=f"dns{i}.json", tool_name="dnsx") for i in range(4)]
    db.add_all(scans)
    db.flush()
    s1, s2, s3, s4 = scans

    def rec(fqdn, rtype, value, scan=None, at=None, created=None, null_observed=False):
        name = svc.get_or_create_name(db, project_id, fqdn)
        row = models.DNSRecord(
            project_id=project_id, name_id=name.id, domain=fqdn, record_type=rtype, value=value,
            scan_id=scan.id if scan else None, observed_at=at, created_at=created or at or T0,
        )
        db.add(row)
        db.flush()
        if null_observed:
            db.execute(update(models.DNSRecord).where(models.DNSRecord.id == row.id).values(observed_at=None))
        return row

    # Moved: the newest SCAN's answers are the batch; the older scan's two
    # (different times) are history; a CNAME is never an address.
    rec("moved.acme.test", "A", "10.0.0.1", s1, T0)
    rec("moved.acme.test", "A", "10.0.0.2", s1, T0 + timedelta(minutes=1))
    rec("moved.acme.test", "A", "10.0.0.3", s2, T0 + timedelta(hours=1))
    rec("moved.acme.test", "CNAME", "edge.cdn.test", s2, T0 + timedelta(hours=2))
    # Imported (no scan): the answers sharing the newest time — a tie of an
    # A and an AAAA; an older one, and one with no observed_at whose
    # created_at is older still.
    rec("imported.acme.test", "A", "10.1.0.1", None, T0)
    rec("imported.acme.test", "A", "10.1.0.2", None, T0 + timedelta(days=1))
    rec("imported.acme.test", "AAAA", "2001:db8::1", None, T0 + timedelta(days=1))
    rec("imported.acme.test", "A", "10.1.0.9", None, None, created=T0 - timedelta(days=1), null_observed=True)
    # No observed_at at all: created_at decides.
    rec("nullts.acme.test", "A", "10.4.0.1", None, None, created=T0, null_observed=True)
    rec("nullts.acme.test", "A", "10.4.0.2", None, None, created=T0 + timedelta(hours=1), null_observed=True)
    # Two scans at the same time: the higher id is the newest; that scan's
    # whole batch is current, an older answer of it included.
    rec("tie.acme.test", "AAAA", "2001:db8::3", s4, T0 + timedelta(hours=1))
    rec("tie.acme.test", "A", "10.3.0.1", s3, T0 + timedelta(hours=3))
    rec("tie.acme.test", "A", "10.3.0.2", s4, T0 + timedelta(hours=3))
    # Newest is an orphan (no scan): the answers at its time are current —
    # a scan's answer at that very time too; an earlier scan's is history.
    rec("mixed.acme.test", "A", "10.2.0.1", s3, T0 + timedelta(hours=5))
    rec("mixed.acme.test", "A", "10.2.0.3", s4, T0 + timedelta(hours=6))
    rec("mixed.acme.test", "A", "10.2.0.2", None, T0 + timedelta(hours=6))
    return {
        "10.0.0.3", "10.1.0.2", "2001:db8::1", "10.4.0.2",
        "2001:db8::3", "10.3.0.2", "10.2.0.2", "10.2.0.3",
    }


def test_current_binding_is_the_two_subquery_rule_record_by_record(db_session, test_project):
    expected = _binding_estate(db_session, test_project.id)
    r = aliased(models.DNSRecord)
    rows = db_session.execute(
        select(r.id, r.value, svc.current_binding_condition(r), _reference_current_binding(r))
        .where(r.project_id == test_project.id)
    ).all()
    assert len(rows) == 16
    for rid, value, new, old in rows:
        assert bool(new) == bool(old), (rid, value, new, old)
    assert {value for _rid, value, new, _old in rows if new} == expected


def test_current_binding_composes_where_it_is_used(db_session, test_project):
    """Nested twice (``shared_address_condition``) and as a filter: the same
    answers as the reference."""
    _binding_estate(db_session, test_project.id)
    # Another name currently at 10.3.0.2 → tie.acme.test shares an address.
    svc.record_observation(db_session, project_id=test_project.id, name="other.acme.test",
                           record_type="A", value="10.3.0.2", observed_at=T0)
    db_session.flush()
    n = models.DNSName
    shared = {
        fqdn for (fqdn,) in db_session.execute(
            select(n.fqdn).where(n.project_id == test_project.id,
                                 svc.shared_address_condition(test_project.id))
        )
    }
    assert shared == {"tie.acme.test", "other.acme.test"}


def test_the_probe_can_use_the_latest_binding_index(db_session, test_project):
    _postgres(db_session)
    _binding_estate(db_session, test_project.id)
    r = aliased(models.DNSRecord)
    stmt = select(func.count()).select_from(r).where(
        r.project_id == test_project.id, svc.current_binding_condition(r),
    )
    compiled = str(stmt.compile(dialect=db_session.get_bind().dialect,
                                compile_kwargs={"literal_binds": True}))
    # The question is whether the probe CAN use the partial index (its
    # predicate implied, its order the probe's): on a tiny table the planner
    # may prefer anything, so the alternatives are taken away.
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    db_session.execute(text("SET LOCAL enable_sort = off"))
    plan = "\n".join(row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}")))
    assert "ix_dns_records_name_latest_binding" in plan, plan


# --- A-R9: the names export --------------------------------------------------------

def _names_estate(db, project_id, count=11):
    """More names than a (patched) chunk, with ties and missing dates in
    both sorted columns."""
    for i in range(count):
        name = svc.get_or_create_name(db, project_id, f"n{(i * 7) % count:02d}.export.test")
        name.last_seen = None if i % 4 == 0 else T0 + timedelta(hours=i % 3)
        name.first_seen = None if i % 5 == 0 else T0 - timedelta(hours=i % 2)
    svc.record_observation(db, project_id=project_id, name="n03.export.test",
                           record_type="A", value="10.9.0.3")
    db.flush()


@pytest.mark.parametrize("sort", ["fqdn", "last_seen", "first_seen"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_the_export_is_the_list_in_its_order_across_chunks(
    client, db_session, test_project, monkeypatch, sort, order,
):
    _names_estate(db_session, test_project.id)
    monkeypatch.setattr(dns_names_routes, "_EXPORT_CHUNK", 3)  # 11 names = 4 chunks
    expected = [
        name.fqdn for name, _in_scope in names_list_service.names_query(
            db_session, test_project.id, search=None, state="all", sort=sort, order=order,
        ).all()
    ]
    assert len(expected) == 11

    seen, engine, record = _statements(db_session)
    event.listen(engine, "before_cursor_execute", record)
    try:
        txt = client.get(f"/api/v1/projects/{test_project.id}/names/export",
                         params={"format": "txt", "sort": sort, "order": order})
        csv_ = client.get(f"/api/v1/projects/{test_project.id}/names/export",
                          params={"format": "csv", "sort": sort, "order": order})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert txt.status_code == 200 and csv_.status_code == 200
    assert txt.text.splitlines() == expected
    rows = [line.split(",") for line in csv_.text.splitlines()[1:]]
    assert [row[0] for row in rows] == expected
    assert {row[0]: row[3] for row in rows}["n03.export.test"] == "10.9.0.3"
    # Read once, never paged by OFFSET (each page re-ran the sort before it).
    assert not [s for s in seen if "OFFSET" in s.upper() and "dns_names" in s], seen


# --- Nitpick: the evidence gap's first N, by address -------------------------------

def test_the_gap_list_is_the_first_hosts_of_the_whole_gap_by_address(db_session, test_project):
    """205 hosts with no port (the ``port_discovery`` gap), inserted so id
    order is NOT address order: the IPv6 hosts first, the IPv4 ones
    descending, and the lowest address last of all."""
    pid = test_project.id
    addresses = (
        ["2001:db8::2", "2001:db8::10", "2001:db8::1"]
        + [f"10.0.{i // 250}.{i % 250 + 2}" for i in range(201)][::-1]
        + ["10.0.0.1"]
    )
    for ip in addresses:
        db_session.add(models.Host(project_id=pid, ip_address=ip, state="up"))
    db_session.flush()

    gap = evidence_gap_hosts(db_session, pid, "port_discovery", limit=200)
    assert gap["total"] == 205  # the whole gap, not the page

    def address_key(ip):
        a = ipaddress.ip_address(ip)
        return (a.version, int(a))

    expected = sorted(addresses, key=address_key)[:200]
    listed = [item["ip_address"] for item in gap["items"]]
    assert listed == expected
    assert listed[0] == "10.0.0.1"          # the last inserted, first by address
    assert not any(":" in ip for ip in listed)  # IPv6 sorts after IPv4, past the cut

    # Under the cap, the IPv6 hosts are in address order (::1, ::2, ::10).
    whole = evidence_gap_hosts(db_session, pid, "port_discovery", limit=500)
    tail = [item["ip_address"] for item in whole["items"]][-3:]
    assert tail == ["2001:db8::1", "2001:db8::2", "2001:db8::10"]
