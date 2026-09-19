"""The import result on the scan row (v2.346.0; design review item 5).

The end of an import must be a durable, actionable summary.  The inventory's
per-scan summary already carried hosts added / re-observed and the per-kind
contribution; it now also carries the conflicts the scan raised and the
ingestion job's quality trio, and can be fetched for one scan by id so the
upload banner shows the same numbers the scan row will.
"""
from datetime import datetime, timezone

from app.db import models
from app.db.models_confidence import ConflictHistory


def _scan(db, project_id, filename):
    scan = models.Scan(project_id=project_id, filename=filename, tool_name="nmap", scan_type="port_scan")
    db.add(scan)
    db.flush()
    return scan


def _observe(db, project_id, scan, ip, *, created):
    host = db.query(models.Host).filter_by(project_id=project_id, ip_address=ip).first()
    if host is None:
        host = models.Host(project_id=project_id, ip_address=ip, state="up")
        db.add(host)
        db.flush()
    db.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan="up",
        discovered_at=datetime.now(timezone.utc), host_created=created,
    ))
    db.flush()
    return host


def test_scan_row_carries_conflicts_and_import_quality(client, db_session, test_project):
    earlier = _scan(db_session, test_project.id, "earlier.xml")
    known = _observe(db_session, test_project.id, earlier, "10.7.0.1", created=True)

    scan = _scan(db_session, test_project.id, "now.xml")
    _observe(db_session, test_project.id, scan, "10.7.0.1", created=False)
    _observe(db_session, test_project.id, scan, "10.7.0.2", created=True)
    _observe(db_session, test_project.id, scan, "10.7.0.3", created=True)
    db_session.add(ConflictHistory(
        host_id=known.id, field_name="os_name", previous_value="Linux", new_value="Windows",
        previous_scan_id=earlier.id, new_scan_id=scan.id,
    ))
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="now.xml", original_filename="now.xml",
        storage_path="/tmp/now.xml", status="completed", scan_id=scan.id,
        skipped_count=3, partial=True, parser_warnings="2 hosts had no address",
    ))
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/scans/", params={"ids": str(scan.id)})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["id"] for row in rows] == [scan.id]
    row = rows[0]
    assert (row["new_hosts"], row["updated_hosts"], row["total_hosts"]) == (2, 1, 3)
    assert row["conflicts"] == 1
    assert row["import_skipped"] == 3
    assert row["import_partial"] is True
    assert row["import_warnings"] == "2 hosts had no address"
    assert row["import_job_id"] is not None

    # The earlier scan raised no conflict and has no job: clean defaults.
    r2 = client.get(f"/api/v1/projects/{test_project.id}/scans/", params={"ids": str(earlier.id)})
    (row2,) = r2.json()
    assert row2["conflicts"] == 0
    assert row2["import_skipped"] == 0
    assert row2["import_partial"] is False
    assert row2["import_job_id"] is None
    assert row2["import_final_format"] is None


def test_scan_row_carries_the_format_chain_as_labels(client, db_session, test_project):
    """v2.358.0 — how the file was read sits beside what it added: an import
    the operator forced to another parser reads differently from a confident
    detection, and that used to be visible only on Ingestion Results."""
    scan = _scan(db_session, test_project.id, "forced.xml")
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="forced.xml", original_filename="forced.xml",
        storage_path="/tmp/forced.xml", status="completed", scan_id=scan.id,
        detected_file_type="nmap_xml", format_override="masscan_xml",
        final_file_type="masscan_xml", source_tool="masscan 1.3",
    ))
    db_session.commit()

    (row,) = client.get(f"/api/v1/projects/{test_project.id}/scans/", params={"ids": str(scan.id)}).json()
    assert row["import_detected_format"] == "Nmap XML"
    assert row["import_format_override"] == row["import_final_format"]
    assert row["import_final_format"] not in (None, "masscan_xml")  # a label, not the registry key
    assert row["import_source_tool"] == "masscan 1.3"


def test_ids_filter_rejects_garbage_and_scopes_to_the_project(client, db_session, test_project):
    scan = _scan(db_session, test_project.id, "a.xml")
    db_session.commit()
    bad = client.get(f"/api/v1/projects/{test_project.id}/scans/", params={"ids": "1,x"})
    assert bad.status_code == 422

    from app.db.models_project import Project
    other = Project(name="other", slug="other-ids", description="")
    db_session.add(other)
    db_session.flush()
    foreign = _scan(db_session, other.id, "b.xml")
    db_session.commit()
    r = client.get(
        f"/api/v1/projects/{test_project.id}/scans/", params={"ids": f"{scan.id},{foreign.id}"},
    )
    assert [row["id"] for row in r.json()] == [scan.id]
