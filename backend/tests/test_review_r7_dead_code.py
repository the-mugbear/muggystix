"""Review 2026-10-10, R7 and backlog H-h: dead code gone, and what the
schema now says instead of the code.

* Every host, scan, import job, parse error and DNS record belongs to a
  project (``e3b7a1c9d5f0``): the boot-time repair that moved project-less
  rows into the lowest-id project is gone, and so are the "no project"
  branches of host dedup and masscan.
* ``GET /scans/{id}/hosts/count`` (no caller) and the whole-file Nessus
  parse paths (only code that read an export whole) are gone.
* One scanner row per identity (``b6e0d4f2a8c3``): a repeat the lookup did
  not find is refused by the database instead of becoming a silent duplicate.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


# ---------------------------------------------------------------------------
# project_id is required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", [
    models.Host, models.Scan, models.IngestionJob, models.ParseError, models.DNSRecord,
])
def test_project_id_is_not_null_on_the_five_tables(model):
    assert model.__table__.columns["project_id"].nullable is False


def test_a_host_without_a_project_cannot_be_stored(db_session, test_project):
    scan = models.Scan(filename="s.xml", scan_type="port_scan", tool_name="nmap", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.Host(ip_address="10.9.9.9", state="up"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_boot_repair_is_gone():
    import app.startup as startup

    assert not hasattr(startup, "ensure_default_project")


def test_dedup_needs_the_project(db_session, test_project):
    from app.services.host_deduplication_service import HostDeduplicationService

    scan = models.Scan(filename="s.xml", scan_type="port_scan", tool_name="nmap", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    with pytest.raises(ValueError):
        HostDeduplicationService(db_session).find_or_create_host("10.9.9.9", scan.id, {}, None)


def test_dedup_matches_an_address_only_within_its_project(db_session, test_project):
    """The no-project branch matched a host by address across every project."""
    from app.services.host_deduplication_service import HostDeduplicationService

    other = models_project_project(db_session, "other-r7")
    scans = []
    for pid in (test_project.id, other.id):
        scan = models.Scan(filename="s.xml", scan_type="port_scan", tool_name="nmap", project_id=pid)
        db_session.add(scan)
        db_session.flush()
        scans.append(scan)
    dedup = HostDeduplicationService(db_session)
    mine = dedup.find_or_create_host("10.9.9.10", scans[0].id, {"state": "up"}, project_id=test_project.id)
    theirs = dedup.find_or_create_host("10.9.9.10", scans[1].id, {"state": "up"}, project_id=other.id)
    assert mine.id != theirs.id
    assert (mine.project_id, theirs.project_id) == (test_project.id, other.id)


def models_project_project(db, slug):
    from app.db.models_project import Project

    project = Project(name=slug, slug=slug)
    db.add(project)
    db.flush()
    return project


def test_masscan_needs_the_project(db_session, tmp_path):
    from app.parsers.masscan_parser import MasscanParser

    path = tmp_path / "m.txt"
    path.write_text("open tcp 80 10.0.0.1 1700000000\n")
    with pytest.raises(ValueError):
        MasscanParser(db_session).parse_file(str(path), "m.txt")


# ---------------------------------------------------------------------------
# dead routes and paths
# ---------------------------------------------------------------------------

def test_the_scan_host_count_route_is_gone(client):
    paths = client.app.openapi()["paths"]
    assert not [p for p in paths if p.endswith("/scans/{scan_id}/hosts/count")]


def test_nessus_is_read_by_streaming_only():
    from app.parsers.nessus_parser import NessusParser

    for gone in ("parse_file", "parse_content", "_parse_nessus_data", "_generate_scan_stats",
                 "_normalize_os_name", "get_supported_extensions"):
        assert not hasattr(NessusParser, gone), gone
    assert hasattr(NessusParser, "iter_file")


def test_the_lease_alias_is_gone():
    from app.services import webhook_dispatcher

    assert not hasattr(webhook_dispatcher, "_FASTPATH_LEASE_SECONDS")


# ---------------------------------------------------------------------------
# H-h: one scanner row per identity
# ---------------------------------------------------------------------------

def _host(db, project_id, ip="10.7.7.7"):
    scan = models.Scan(filename="s.xml", scan_type="vuln", tool_name="nessus", project_id=project_id)
    db.add(scan)
    db.flush()
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(host)
    db.flush()
    return scan, host


def _vuln(scan, host, **over):
    values = dict(
        host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
        plugin_id="10863", title="SSL Certificate Information",
        severity=VulnerabilitySeverity.INFO,
    )
    values.update(over)
    return Vulnerability(**values)


def test_a_second_row_with_one_identity_is_refused(db_session, test_project):
    """An insert path that forgot to flush wrote a silent duplicate; now the
    database refuses it.  NULL port / name are one value (NULLS NOT DISTINCT)."""
    scan, host = _host(db_session, test_project.id)
    db_session.add(_vuln(scan, host))
    db_session.flush()
    db_session.add(_vuln(scan, host))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_rows_that_differ_in_any_part_of_the_identity_are_kept(db_session, test_project):
    """Nikto's one id for five missing headers (title in the identity), a
    finding on two names behind one address, and an id-less row keyed by
    title all stay distinct."""
    scan, host = _host(db_session, test_project.id)
    name_a = models.DNSName(project_id=test_project.id, fqdn="a.example.test")
    name_b = models.DNSName(project_id=test_project.id, fqdn="b.example.test")
    db_session.add_all([name_a, name_b])
    db_session.flush()
    db_session.add_all([
        _vuln(scan, host, source=VulnerabilitySource.NIKTO, plugin_id="013587", title="Missing header: X-Frame-Options"),
        _vuln(scan, host, source=VulnerabilitySource.NIKTO, plugin_id="013587", title="Missing header: CSP"),
        _vuln(scan, host, source=VulnerabilitySource.NIKTO, plugin_id="999", title="t", name_id=name_a.id),
        _vuln(scan, host, source=VulnerabilitySource.NIKTO, plugin_id="999", title="t", name_id=name_b.id),
        _vuln(scan, host, plugin_id=None, title="no id one"),
        _vuln(scan, host, plugin_id=None, title="no id two"),
    ])
    db_session.flush()


def test_the_upsert_still_finds_an_in_scan_repeat(db_session, test_project):
    """The lookup-then-insert path is unchanged: a repeat in one scan updates
    the row it wrote instead of hitting the index."""
    from app.parsers.parser_utils import upsert_vulnerability

    scan, host = _host(db_session, test_project.id)
    first = upsert_vulnerability(
        db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.OPENVAS,
        title="Weak cipher", severity=VulnerabilitySeverity.MEDIUM, plugin_id="1.3.6.1",
    )
    again = upsert_vulnerability(
        db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.OPENVAS,
        title="Weak cipher", severity=VulnerabilitySeverity.HIGH, plugin_id="1.3.6.1",
    )
    assert first.id == again.id
    assert db_session.query(Vulnerability).filter_by(host_id=host.id).count() == 1
