"""Scan sightings: one row per (thing, scan that reported it), and the one
step both scan deletes run (``scan_sightings.release_scan``).

``scripts_v2`` / ``host_scripts_v2`` / ``host_attributes`` / ``vulnerabilities``
hold one row per thing and name only the scan that first recorded it, so a
scan delete used to GUESS which other scan had seen a row (the newest scan
with history on its port or host).  The guess lost a row three scans had
reported when the first and then the last of them were deleted, and kept —
under a scan that never reported it — a row only the deleted scan had.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, text

from app.core.config import settings
from app.db import models
from app.db.models_findings import Finding
from app.db.models_vulnerability import (
    HostAttribute,
    HostAttributeSighting,
    Vulnerability,
    VulnerabilitySeverity,
    VulnerabilitySighting,
    VulnerabilitySource,
)
from app.parsers.parser_utils import upsert_vulnerability
from app.services import scan_sightings
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.ingestion_service import delete_partial_scan
from app.services.misconfig_backfill import backfill_misconfigs
from app.services.vulnerability_service import VulnerabilityService
from tests.conftest import engine
from tests.ingestion_job_harness import run_file
from tests.test_ingestion_partial_scan import THREE, nessus_file
from tests.test_parser_architecture_review import _host, _import_nmap, _nmap_xml, _port
from tests.test_vuln_dedup import _nessus_host, _vuln

PATHS = ("cleanup", "route")

SCRIPT = '<script id="http-title" output="{out}"/>'
HOSTSCRIPT = '<hostscript><script id="smb-os-discovery" output="{out}"/></hostscript>'


def _delete_scan(path, db, client, pid, scan_id):
    """Delete a scan through the automatic cleanup or through the Scans page."""
    db.commit()
    if path == "cleanup":
        delete_partial_scan(db, scan_id)
        db.commit()
    else:
        response = client.delete(f"/api/v1/projects/{pid}/scans/{scan_id}")
        assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(models.Scan, scan_id) is None


def _scan(db, pid, name, tool="nmap"):
    scan = models.Scan(filename=name, tool_name=tool, scan_type=tool, project_id=pid)
    db.add(scan)
    db.flush()
    return scan


def _sightings(db, model, column, thing_id):
    return sorted(
        row[0] for row in db.query(model.scan_id).filter(getattr(model, column) == thing_id)
    )


def _nmap_scan(db, project, tmp_path, name, *, with_scripts=True, ip="10.71.0.1"):
    out = name
    return _import_nmap(db, project, tmp_path, _nmap_xml(_host(
        ip,
        _port("80", script=SCRIPT.format(out=out) if with_scripts else ""),
        HOSTSCRIPT.format(out=out) if with_scripts else "",
    )), f"{name}.xml").id


def _nessus_scan(db, pid, host, name, *, findings=True):
    scan = _scan(db, pid, f"{name}.nessus", tool="nessus")
    reported = _nessus_host(host.ip_address, [_vuln("33850", 22)] if findings else [])
    if findings:
        reported.operating_system = "Linux Kernel 5.15"
    VulnerabilityService(db).process_nessus_vulnerabilities(host, reported, scan)
    db.flush()
    return scan.id


# ---------------------------------------------------------------------------
# Three scans report the same row; the first, then the last, is deleted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", PATHS)
def test_scripts_three_scans_reported_survive_deleting_the_first_then_the_last(
    path, client, db_session, test_project, tmp_path,
):
    """The guess moved the row from A to C (the newest scan with history on
    the port), and deleting C then found only an EARLIER scan — B — and let
    the row go with C, although B had reported it."""
    pid = test_project.id
    a, b, c = (_nmap_scan(db_session, test_project, tmp_path, name) for name in "abc")
    script = db_session.query(models.Script).one()
    host_script = db_session.query(models.HostScript).one()
    script_id, host_script_id = script.id, host_script.id
    assert (script.scan_id, host_script.scan_id) == (a, a)
    assert _sightings(db_session, models.ScriptSighting, "script_id", script_id) == [a, b, c]
    assert _sightings(db_session, models.HostScriptSighting, "host_script_id", host_script_id) == [a, b, c]

    _delete_scan(path, db_session, client, pid, a)
    # Exactly the earliest scan left that reported it — not the newest.
    assert db_session.get(models.Script, script_id).scan_id == b
    assert db_session.get(models.HostScript, host_script_id).scan_id == b

    _delete_scan(path, db_session, client, pid, c)
    script, host_script = db_session.get(models.Script, script_id), db_session.get(models.HostScript, host_script_id)
    assert script is not None and host_script is not None
    assert (script.scan_id, host_script.scan_id) == (b, b)
    assert _sightings(db_session, models.ScriptSighting, "script_id", script_id) == [b]
    assert _sightings(db_session, models.HostScriptSighting, "host_script_id", host_script_id) == [b]


@pytest.mark.parametrize("path", PATHS)
def test_nessus_rows_three_scans_reported_survive_deleting_the_first_then_the_last(
    path, client, db_session, test_project,
):
    """A vulnerability's pointers went A → C on the first delete (its "last
    seen by"), and the cleanup of C then deleted the row as "only C's"."""
    pid = test_project.id
    host = models.Host(ip_address="10.71.0.2", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    a, b, c = (_nessus_scan(db_session, pid, host, name) for name in "abc")
    vuln = db_session.query(Vulnerability).one()
    attribute = db_session.query(HostAttribute).filter_by(attribute_type="os_name").one()
    vuln_id, attribute_id = vuln.id, attribute.id
    assert (vuln.scan_id, vuln.last_seen_scan_id, attribute.scan_id) == (a, c, a)
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", vuln_id) == [a, b, c]
    assert _sightings(db_session, HostAttributeSighting, "host_attribute_id", attribute_id) == [a, b, c]

    _delete_scan(path, db_session, client, pid, a)
    vuln = db_session.get(Vulnerability, vuln_id)
    assert (vuln.scan_id, vuln.last_seen_scan_id) == (b, c)
    assert db_session.get(HostAttribute, attribute_id).scan_id == b

    _delete_scan(path, db_session, client, pid, c)
    vuln, attribute = db_session.get(Vulnerability, vuln_id), db_session.get(HostAttribute, attribute_id)
    assert vuln is not None and attribute is not None
    assert (vuln.scan_id, vuln.last_seen_scan_id, attribute.scan_id) == (b, b, b)
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", vuln_id) == [b]


# ---------------------------------------------------------------------------
# What only the deleted scan reported
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", PATHS)
def test_a_script_only_the_deleted_scan_reported_goes_although_a_later_scan_saw_the_port(
    path, client, db_session, test_project, tmp_path,
):
    """Scan B scanned the same port and host and reported NO script.  The
    guess handed A's scripts to B, which never reported them."""
    pid = test_project.id
    a = _nmap_scan(db_session, test_project, tmp_path, "a")
    b = _nmap_scan(db_session, test_project, tmp_path, "b", with_scripts=False)
    assert db_session.query(models.Script).count() == 1 and db_session.query(models.HostScript).count() == 1

    _delete_scan(path, db_session, client, pid, a)

    assert db_session.query(models.Script).count() == 0
    assert db_session.query(models.HostScript).count() == 0
    # The port and the host are B's too, and stay.
    assert db_session.query(models.Port).count() == 1
    assert [row.scan_id for row in db_session.query(models.PortScanHistory)] == [b]


def test_the_cleanup_deletes_an_observation_only_its_scan_reported_unless_a_finding_refers_to_it(
    db_session, test_project,
):
    pid = test_project.id
    host = models.Host(ip_address="10.71.0.3", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    earlier = _nessus_scan(db_session, pid, host, "earlier", findings=False)
    partial = _scan(db_session, pid, "partial.nessus", tool="nessus")
    VulnerabilityService(db_session).process_nessus_vulnerabilities(
        host, _nessus_host(host.ip_address, [_vuln("33850", 22), _vuln("42873", 443)]), partial)
    promoted = db_session.query(Vulnerability).filter_by(plugin_id="42873").one()
    db_session.add(Finding(
        project_id=pid, title="Weak ciphers", severity="high", status="open", source="scanner",
        vuln_id=promoted.id,
    ))
    db_session.commit()
    partial_id, promoted_id = partial.id, promoted.id

    removed = delete_partial_scan(db_session, partial_id)
    db_session.commit()
    db_session.expire_all()

    assert removed["observations"] == 1
    kept = db_session.query(Vulnerability).one()
    assert kept.id == promoted_id and (kept.scan_id, kept.last_seen_scan_id) == (None, None)
    assert db_session.get(models.Scan, earlier) is not None


def test_a_hand_delete_removes_an_observation_only_its_scan_reported(
    client, db_session, test_project,
):
    pid = test_project.id
    host = models.Host(ip_address="10.71.0.4", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    scan = _nessus_scan(db_session, pid, host, "only")
    vuln_id = db_session.query(Vulnerability.id).scalar()

    _delete_scan("route", db_session, client, pid, scan)

    # The host stays (nothing says this scan brought it); what only this
    # scan reported on it goes, as the cleanup of a failed import removes it.
    assert db_session.get(models.Host, host.id) is not None
    assert db_session.get(Vulnerability, vuln_id) is None
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", vuln_id) == []
    assert db_session.query(HostAttribute).count() == 0


def test_a_row_that_names_another_scan_or_has_no_sighting_is_not_deleted(db_session, test_project):
    """Rows written without sightings (a seed, a script): nothing says the
    scan being deleted reported them, so the release leaves them alone."""
    pid = test_project.id
    first, later = _scan(db_session, pid, "first.xml"), _scan(db_session, pid, "later.xml")
    host = models.Host(ip_address="10.71.0.5", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    port = models.Port(host_id=host.id, port_number=80, protocol="tcp", state="open")
    db_session.add(port)
    db_session.flush()
    unsighted = models.Script(port_id=port.id, script_id="http-title", output="x", scan_id=later.id)
    # First recorded by ``first``; only ``later`` has a sighting of it.
    named = models.Script(port_id=port.id, script_id="http-server-header", output="y", scan_id=first.id)
    db_session.add_all([unsighted, named])
    db_session.flush()
    scan_sightings.see(db_session, scan_sightings.SCRIPT, named.id, later.id)
    db_session.commit()
    unsighted_id, named_id, first_id = unsighted.id, named.id, first.id

    released = scan_sightings.release_scan(db_session, later.id)
    db_session.query(models.Scan).filter_by(id=later.id).delete()
    db_session.commit()
    db_session.expire_all()

    assert released["deleted"] == 0
    # The pointer was cleared by the foreign key; the row is still there.
    assert db_session.get(models.Script, unsighted_id).scan_id is None
    assert db_session.get(models.Script, named_id).scan_id == first_id


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def test_a_repeat_within_one_scan_is_one_sighting_across_a_commit(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid, "merged.xml")
    dedup = HostDeduplicationService(db_session)
    host = dedup.find_or_create_host("10.71.0.6", scan.id, {"state": "up"}, project_id=pid)
    port = dedup.find_or_create_port(host.id, scan.id, {"port_number": 80, "protocol": "tcp", "state": "open"})
    scan_id, host_id, port_id = scan.id, host.id, port.id

    def report(out):
        dedup.add_or_update_script(port_id, scan_id, {"script_id": "http-title", "output": out})
        dedup.add_or_update_host_script(host_id, scan_id, {"script_id": "smb-os-discovery", "output": out})
        upsert_vulnerability(
            db=db_session, host_id=host_id, scan_id=scan_id, source=VulnerabilitySource.NMAP,
            title="Anonymous FTP", severity=VulnerabilitySeverity.MEDIUM, plugin_id="ftp-anon",
        )

    report("first")
    db_session.commit()            # the heartbeat
    report("second")
    db_session.commit()

    assert db_session.query(models.Script).one().output == "second"
    assert db_session.query(models.ScriptSighting).count() == 1
    assert db_session.query(models.HostScriptSighting).count() == 1
    assert db_session.query(VulnerabilitySighting).count() == 1


def test_a_record_rolled_back_by_its_savepoint_leaves_no_sighting(db_session, test_project):
    """Scan B re-observes what scan A recorded, inside a record savepoint that
    is then rolled back: B never reported those rows."""
    pid = test_project.id
    a, b = _scan(db_session, pid, "a.xml"), _scan(db_session, pid, "b.xml")
    dedup = HostDeduplicationService(db_session)
    host = dedup.find_or_create_host("10.71.0.7", a.id, {"state": "up"}, project_id=pid)
    port = dedup.find_or_create_port(host.id, a.id, {"port_number": 80, "protocol": "tcp", "state": "open"})

    def report(scan_id):
        dedup.add_or_update_script(port.id, scan_id, {"script_id": "http-title", "output": "x"})
        dedup.add_or_update_host_script(host.id, scan_id, {"script_id": "smb-os-discovery", "output": "x"})
        upsert_vulnerability(
            db=db_session, host_id=host.id, scan_id=scan_id, source=VulnerabilitySource.NMAP,
            title="Anonymous FTP", severity=VulnerabilitySeverity.MEDIUM, plugin_id="ftp-anon",
        )

    report(a.id)
    db_session.flush()
    record = db_session.begin_nested()
    report(b.id)
    assert db_session.query(models.ScriptSighting).count() == 2
    record.rollback()
    dedup.discard_rolled_back_state()

    for model in (models.ScriptSighting, models.HostScriptSighting, VulnerabilitySighting):
        assert [row.scan_id for row in db_session.query(model)] == [a.id]


def test_masscan_banners_are_sighted_in_one_statement_per_batch(db_session, test_project):
    from app.parsers.masscan_parser import MasscanParser

    pid = test_project.id
    first, second = _scan(db_session, pid, "first.json", "masscan"), _scan(db_session, pid, "second.json", "masscan")
    for i in range(3):
        host = models.Host(ip_address=f"10.71.1.{i + 1}", state="up", project_id=pid)
        db_session.add(host)
        db_session.flush()
        db_session.add(models.Port(host_id=host.id, port_number=80, protocol="tcp", state="open"))
    db_session.flush()

    def store(scan_id):
        parser = MasscanParser(db_session)
        parser._project_id = pid
        parser._banners = {(f"10.71.1.{i + 1}", 80, "tcp", "http.server"): "nginx" for i in range(3)}
        statements = []

        def _before(conn, cursor, statement, params, context, executemany):
            if "script_sightings" in statement:
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", _before)
        try:
            parser._store_banners(scan_id)
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return len(statements)

    assert store(first.id) == 1
    assert store(second.id) == 1          # the re-observation is sighted too
    per_script = {}
    for row in db_session.query(models.ScriptSighting):
        per_script.setdefault(row.script_id, []).append(row.scan_id)
    assert len(per_script) == 3
    assert all(sorted(scans) == [first.id, second.id] for scans in per_script.values())
    assert {s.scan_id for s in db_session.query(models.Script)} == {first.id}


# ---------------------------------------------------------------------------
# Nessus
# ---------------------------------------------------------------------------

def _sighting_statements(fn):
    statements = []

    def _before(conn, cursor, statement, params, context, executemany):
        if "INSERT INTO vulnerability_sightings" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return len(statements)


def test_nessus_writes_a_batchs_sightings_in_one_statement(db_session, test_project):
    pid = test_project.id
    host = models.Host(ip_address="10.71.0.8", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    findings = [_vuln(str(20000 + i), 443) for i in range(250)]
    batches = -(-len(findings) // VulnerabilityService._NESSUS_FINDING_BATCH)

    first = _scan(db_session, pid, "first.nessus", "nessus")
    wrote = _sighting_statements(lambda: VulnerabilityService(db_session).process_nessus_vulnerabilities(
        host, _nessus_host(host.ip_address, findings), first))
    assert wrote == batches == 3
    assert db_session.query(VulnerabilitySighting).filter_by(scan_id=first.id).count() == 250

    # A second scan re-observes all of them: still one statement per batch.
    second = _scan(db_session, pid, "second.nessus", "nessus")
    wrote = _sighting_statements(lambda: VulnerabilityService(db_session).process_nessus_vulnerabilities(
        host, _nessus_host(host.ip_address, findings), second))
    assert wrote == batches
    assert db_session.query(VulnerabilitySighting).filter_by(scan_id=second.id).count() == 250
    assert db_session.query(Vulnerability).count() == 250


def test_a_nessus_batch_retried_finding_by_finding_still_sights_each_finding(
    db_session, test_project, monkeypatch,
):
    pid = test_project.id
    host = models.Host(ip_address="10.71.0.9", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    scan = _scan(db_session, pid, "retry.nessus", "nessus")
    real = scan_sightings.see_many
    failed = []

    def fail_the_batch_once(db, kind, pairs):
        pairs = list(pairs)
        if kind == scan_sightings.VULNERABILITY and len(pairs) > 1 and not failed:
            failed.append(True)
            raise RuntimeError("simulated batch failure")
        return real(db, kind, pairs)

    monkeypatch.setattr(scan_sightings, "see_many", fail_the_batch_once)
    stats = VulnerabilityService(db_session).process_nessus_vulnerabilities(
        host, _nessus_host(host.ip_address, [_vuln(str(30000 + i), 443) for i in range(5)]), scan)

    assert failed and stats["total"] == 5 and stats["write_failures"] == 0
    assert db_session.query(Vulnerability).count() == 5
    assert db_session.query(VulnerabilitySighting).filter_by(scan_id=scan.id).count() == 5


def test_a_nessus_import_sights_every_row_across_its_batches_and_a_re_import_adds_one_each(
    db_session, test_project, tmp_path, monkeypatch,
):
    """One host per commit: the session is emptied (``expunge_all``) between
    hosts, which must neither lose nor repeat a sighting."""
    monkeypatch.setattr(settings, "NESSUS_COMMIT_BATCH_SIZE", 1)
    pid = test_project.id

    first = run_file(db_session, pid, nessus_file(tmp_path, THREE, name="first.nessus"))
    assert first.status == "completed", first.error_message
    vulns = db_session.query(Vulnerability).all()
    attributes = db_session.query(HostAttribute).all()
    assert len(vulns) == 3 and attributes
    assert [(s.vulnerability_id, s.scan_id) for s in db_session.query(VulnerabilitySighting).order_by(
        VulnerabilitySighting.vulnerability_id)] == [(v.id, first.scan_id) for v in sorted(vulns, key=lambda v: v.id)]
    assert db_session.query(HostAttributeSighting).count() == len(attributes)

    second = run_file(db_session, pid, nessus_file(tmp_path, THREE, name="second.nessus"))
    assert second.status == "completed", second.error_message
    db_session.expire_all()
    assert db_session.query(Vulnerability).count() == 3
    assert db_session.query(HostAttribute).count() == len(attributes)
    for vuln in db_session.query(Vulnerability):
        assert (vuln.scan_id, vuln.last_seen_scan_id) == (first.scan_id, second.scan_id)
        assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", vuln.id) == [
            first.scan_id, second.scan_id]
    for attribute in db_session.query(HostAttribute):
        assert attribute.scan_id == first.scan_id
        assert _sightings(db_session, HostAttributeSighting, "host_attribute_id", attribute.id) == [
            first.scan_id, second.scan_id]


# ---------------------------------------------------------------------------
# The misconfiguration backfill names an OLD scan
# ---------------------------------------------------------------------------

def test_the_backfill_sights_the_old_scan_and_does_not_move_the_first_recorder(db_session, test_project):
    pid = test_project.id
    old, newer = _scan(db_session, pid, "old.xml"), _scan(db_session, pid, "newer.xml")
    host = models.Host(project_id=pid, ip_address="10.71.0.10", state="up")
    db_session.add(host)
    db_session.flush()
    vnc = models.Port(host_id=host.id, port_number=5900, protocol="tcp", state="open")
    db_session.add(vnc)
    db_session.flush()
    db_session.add(models.Script(
        port_id=vnc.id, scan_id=old.id, script_id="vnc-info",
        output="\n  Protocol version: 3.8\n  Security types: \n    None (1)",
    ))
    # The newer scan already recorded the check the old script implies.
    from app.services.misconfig_checks import record_misconfig
    recorded = record_misconfig(
        db_session, check_id="vnc_no_auth", host_id=host.id, scan_id=newer.id,
        source=VulnerabilitySource.NMAP, port_id=vnc.id, evidence="vnc-info: None (1)",
    )
    db_session.flush()
    assert (recorded.scan_id, recorded.last_seen_scan_id) == (newer.id, newer.id)

    backfill_misconfigs(db_session, project_id=pid)
    backfill_misconfigs(db_session, project_id=pid)      # idempotent
    db_session.flush()

    row = db_session.query(Vulnerability).one()
    assert row.scan_id == newer.id                        # first recorder unchanged
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", row.id) == [old.id, newer.id]

    # A script whose first recorder is gone names no scan: nothing to sight,
    # and the stored "last seen by" is kept.
    db_session.query(models.Script).update({"scan_id": None})
    row.last_seen_scan_id = newer.id
    db_session.flush()
    backfill_misconfigs(db_session, project_id=pid)
    db_session.flush()
    db_session.expire_all()
    row = db_session.query(Vulnerability).one()
    assert (row.scan_id, row.last_seen_scan_id) == (newer.id, newer.id)
    assert db_session.query(VulnerabilitySighting).count() == 2


# ---------------------------------------------------------------------------
# The migration's back-fill
# ---------------------------------------------------------------------------

def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "c2f9d7a4e1b3_scan_sightings.py"
    spec = importlib.util.spec_from_file_location("scan_sightings_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_backfill_gives_existing_rows_the_sightings_the_old_rules_implied(db_session, test_project):
    """Rows as they are before the revision: no sightings.  Each gets one for
    the scan it names; a vulnerability one for its "last seen by"; a script,
    host script or attribute one for the scan the old guess would have moved
    it to (the newest other scan with history on its port / host that is
    later, or any such scan when the row was touched again)."""
    pid = test_project.id
    a, b, c = (_scan(db_session, pid, f"{name}.xml") for name in "abc")
    then = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = then + timedelta(hours=1)
    host = models.Host(ip_address="10.71.0.11", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    ports = [models.Port(host_id=host.id, port_number=n, protocol="tcp", state="open") for n in (80, 443, 22)]
    db_session.add_all(ports)
    db_session.flush()
    for scan in (a, b, c):
        db_session.add(models.HostScanHistory(host_id=host.id, scan_id=scan.id, state_at_scan="up"))
    for port in ports[:2]:
        for scan in (a, b):
            db_session.add(models.PortScanHistory(port_id=port.id, scan_id=scan.id, state_at_scan="open"))
    # Port 22: only scan A has history.
    db_session.add(models.PortScanHistory(port_id=ports[2].id, scan_id=a.id, state_at_scan="open"))

    def script(port, name, scan, last_seen=then):
        row = models.Script(port_id=port.id, script_id=name, output="x", scan_id=scan.id,
                            first_seen=then, last_seen=last_seen)
        db_session.add(row)
        return row

    first_of_two = script(ports[0], "http-title", a)                     # B is later: guessed
    last_of_two = script(ports[1], "ssl-cert", b)                        # A is earlier, untouched: alone
    touched = script(ports[1], "http-title", b, last_seen=later)         # A is earlier, touched again: guessed
    alone = script(ports[2], "ssh-hostkey", a)                           # no other history
    host_script = models.HostScript(host_id=host.id, script_id="smb-os-discovery", output="x",
                                    scan_id=a.id, first_seen=then, last_seen=then)
    barely = HostAttribute(host_id=host.id, attribute_type="os_name", value="Linux", source="nessus",
                           scan_id=c.id, first_seen=then, last_seen=then + timedelta(milliseconds=5))
    re_reported = HostAttribute(host_id=host.id, attribute_type="hostname", value="h", source="nessus",
                                scan_id=c.id, first_seen=then, last_seen=later)
    seen_twice = Vulnerability(host_id=host.id, title="t1", severity=VulnerabilitySeverity.HIGH,
                               source=VulnerabilitySource.NESSUS, plugin_id="1", scan_id=a.id,
                               last_seen_scan_id=c.id, first_seen=then, last_seen=later)
    seen_once = Vulnerability(host_id=host.id, title="t2", severity=VulnerabilitySeverity.HIGH,
                              source=VulnerabilitySource.NESSUS, plugin_id="2", scan_id=b.id,
                              last_seen_scan_id=b.id, first_seen=then, last_seen=then)
    detached = Vulnerability(host_id=host.id, title="t3", severity=VulnerabilitySeverity.HIGH,
                             source=VulnerabilitySource.NESSUS, plugin_id="3", scan_id=None,
                             last_seen_scan_id=None)
    db_session.add_all([host_script, barely, re_reported, seen_twice, seen_once, detached])
    db_session.flush()
    assert db_session.query(models.ScriptSighting).count() == 0

    for statement in _migration().BACKFILL:
        db_session.execute(text(statement))
    for statement in _migration().BACKFILL:          # safe to repeat
        db_session.execute(text(statement))

    def scripts(row):
        return _sightings(db_session, models.ScriptSighting, "script_id", row.id)

    assert scripts(first_of_two) == [a.id, b.id]
    assert scripts(last_of_two) == [b.id]
    assert scripts(touched) == [a.id, b.id]
    assert scripts(alone) == [a.id]
    # The newest later scan with history on the host.
    assert _sightings(db_session, models.HostScriptSighting, "host_script_id", host_script.id) == [a.id, c.id]
    # Two clock reads at insert are not "touched again"; an hour is.
    assert _sightings(db_session, HostAttributeSighting, "host_attribute_id", barely.id) == [c.id]
    assert _sightings(db_session, HostAttributeSighting, "host_attribute_id", re_reported.id) == [b.id, c.id]
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", seen_twice.id) == [a.id, c.id]
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", seen_once.id) == [b.id]
    assert _sightings(db_session, VulnerabilitySighting, "vulnerability_id", detached.id) == []
    # The scan a row names is its earliest sighting; the other one is dated
    # by the row's last_seen.
    seen_at = dict(db_session.query(models.ScriptSighting.scan_id, models.ScriptSighting.seen_at).filter_by(
        script_id=touched.id))
    assert seen_at[b.id] == then and seen_at[a.id] == later

    # ...so a delete does to these rows what it did before the revision.
    a_id, b_id, c_id = a.id, b.id, c.id
    first_of_two_id, alone_id, host_script_id, seen_twice_id = (
        first_of_two.id, alone.id, host_script.id, seen_twice.id)
    scan_sightings.release_scan(db_session, a_id)
    db_session.query(models.Scan).filter_by(id=a_id).delete()
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(models.Script, first_of_two_id).scan_id == b_id
    assert db_session.get(models.Script, alone_id) is None
    assert db_session.get(models.HostScript, host_script_id).scan_id == c_id
    assert db_session.get(Vulnerability, seen_twice_id).scan_id == c_id


# ---------------------------------------------------------------------------
# The deletion preview says what the delete does
# ---------------------------------------------------------------------------

def test_the_preview_counts_exactly_the_observations_the_delete_removes(
    client, db_session, test_project,
):
    pid = test_project.id
    target, other = _scan(db_session, pid, "target.nessus", "nessus"), _scan(db_session, pid, "other.nessus", "nessus")
    now = datetime.now(timezone.utc)

    def host(ip, *scans):
        row = models.Host(project_id=pid, ip_address=ip, state="up")
        db_session.add(row)
        db_session.flush()
        for scan in scans:
            db_session.add(models.HostScanHistory(
                host_id=row.id, scan_id=scan.id, state_at_scan="up", discovered_at=now))
        return row

    shared, orphan = host("10.71.2.1", target, other), host("10.71.2.2", target)

    def report(row, plugin, *scans):
        for scan in scans:
            upsert_vulnerability(
                db=db_session, host_id=row.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                title=f"plugin {plugin}", severity=VulnerabilitySeverity.HIGH, plugin_id=plugin,
            )

    report(shared, "1", target)             # only the target reported it: goes with it
    report(shared, "2", target)             # the same
    report(shared, "3", target, other)      # the other scan reported it too: handed over
    report(shared, "4", other)              # never the target's
    report(orphan, "5", target)             # goes with its host
    # Written before sightings existed: "last seen by" is the only witness.
    db_session.add(Vulnerability(
        host_id=shared.id, title="plugin 6", plugin_id="6", severity=VulnerabilitySeverity.HIGH,
        source=VulnerabilitySource.NESSUS, scan_id=target.id, last_seen_scan_id=other.id,
    ))
    db_session.commit()
    target_id, other_id, shared_id = target.id, other.id, shared.id

    impact = client.get(f"/api/v1/projects/{pid}/scans/{target_id}/deletion-impact")
    assert impact.status_code == 200, impact.text
    assert impact.json()["vulnerabilities_removed"] == 2
    assert impact.json()["vulnerabilities_kept"] == 0
    assert impact.json()["hosts_removed"] == 1
    on_shared = db_session.query(Vulnerability).filter_by(host_id=shared_id).count()

    _delete_scan("route", db_session, client, pid, target_id)

    rows = {v.plugin_id: (v.scan_id, v.last_seen_scan_id)
            for v in db_session.query(Vulnerability).filter_by(host_id=shared_id)}
    assert rows == {
        "3": (other_id, other_id), "4": (other_id, other_id), "6": (other_id, other_id),
    }
    assert on_shared - len(rows) == impact.json()["vulnerabilities_removed"]
    assert db_session.query(Vulnerability).filter_by(plugin_id="5").count() == 0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_first_recorded_by_pointers_are_cleared_not_cascaded_and_sightings_cascade():
    for model in (models.Script, models.HostScript, HostAttribute):
        column = model.__table__.c.scan_id
        assert column.nullable is True
        assert [fk.ondelete for fk in column.foreign_keys] == ["SET NULL"]
    for model in (models.ScriptSighting, models.HostScriptSighting, HostAttributeSighting, VulnerabilitySighting):
        table = model.__table__
        assert len(table.primary_key.columns) == 2 and "scan_id" in table.primary_key.columns
        assert sorted(fk.ondelete for fk in table.foreign_keys) == ["CASCADE", "CASCADE"]
        assert table.c.seen_at.nullable is False
        assert any(list(index.columns) == [table.c.scan_id] for index in table.indexes)
