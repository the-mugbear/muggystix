"""Upload batches, duplicate refusal, and the /scans change marker (v2.335.0).

Agents splitting a 90k-host scope into hundreds of chunks flattened /scans
into an unnavigable list, and re-uploading an identical file silently made a
second, indistinguishable scan — every scan counter moved, no data was added.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_project import Project
from app.services.ingestion_service import carry_upload_identity

NMAP_A = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"><!-- a --></nmaprun>\n'
NMAP_B = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"><!-- b --></nmaprun>\n'


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _upload(client, project, data, name="scan.xml", **form):
    return client.post(
        f"/api/v1/projects/{project.id}/upload/",
        files={"file": (name, data, "text/xml")},
        data={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()},
    )


# ---------------------------------------------------------------------------
# Duplicate refusal
# ---------------------------------------------------------------------------

def test_identical_file_still_queued_is_refused(client, db_session, test_project):
    first = _upload(client, test_project, NMAP_A)
    assert first.status_code == 200, first.text

    again = _upload(client, test_project, NMAP_A, name="renamed.xml")
    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["code"] == "duplicate_scan"
    assert detail["job_id"] == first.json()["job_id"]
    assert detail["scan_id"] is None
    assert "still processing" in detail["message"]
    # Nothing was created for the refused copy.
    assert db_session.query(models.IngestionJob).filter_by(project_id=test_project.id).count() == 1


def test_identical_to_an_existing_scan_is_refused_and_named(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="sweep.xml", content_sha256=_sha(NMAP_A))
    db_session.add(scan)
    db_session.commit()

    r = _upload(client, test_project, NMAP_A)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["scan_id"] == scan.id
    assert "sweep.xml" in r.json()["detail"]["message"]


def test_operator_can_import_again_deliberately(client, db_session, test_project):
    db_session.add(models.Scan(project_id=test_project.id, filename="s.xml", content_sha256=_sha(NMAP_A)))
    db_session.commit()
    r = _upload(client, test_project, NMAP_A, allow_duplicate=True)
    assert r.status_code == 200, r.text


def test_what_does_not_count_as_a_duplicate(client, db_session, test_project):
    other = Project(name="Other engagement", slug="other-engagement")
    db_session.add(other)
    db_session.commit()
    # Same bytes in ANOTHER project, and a FAILED job here (the operator is
    # retrying) — neither blocks.
    db_session.add(models.Scan(project_id=other.id, filename="x.xml", content_sha256=_sha(NMAP_A)))
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="a.xml", original_filename="a.xml",
        storage_path="/nonexistent", status="failed", content_sha256=_sha(NMAP_A),
    ))
    db_session.commit()
    assert _upload(client, test_project, NMAP_A).status_code == 200
    # Different content is never a duplicate.
    assert _upload(client, test_project, NMAP_B).status_code == 200


def test_completed_job_stamps_batch_and_digest_onto_its_scan(db_session, test_project):
    batch = models.ScanBatch(project_id=test_project.id, label="nmap-tcp")
    scan = models.Scan(project_id=test_project.id, filename="c.xml")
    db_session.add_all([batch, scan])
    db_session.commit()
    job = models.IngestionJob(
        project_id=test_project.id, filename="c.xml", original_filename="c.xml",
        storage_path="/x", status="completed", scan_id=scan.id,
        batch_id=batch.id, content_sha256=_sha(NMAP_A),
    )
    db_session.add(job)
    db_session.commit()

    carry_upload_identity(db_session, job)
    db_session.commit()
    db_session.refresh(scan)
    assert scan.batch_id == batch.id
    assert scan.content_sha256 == _sha(NMAP_A)


# ---------------------------------------------------------------------------
# Operator batches
# ---------------------------------------------------------------------------

def test_multi_file_upload_joins_a_batch(client, db_session, test_project):
    created = client.post(f"/api/v1/projects/{test_project.id}/scans/batches", json={"label": "2 files"})
    assert created.status_code == 201, created.text
    batch_id = created.json()["id"]

    for data in (NMAP_A, NMAP_B):
        assert _upload(client, test_project, data, batch_id=batch_id).status_code == 200
    jobs = db_session.query(models.IngestionJob).filter_by(project_id=test_project.id).all()
    assert {j.batch_id for j in jobs} == {batch_id}


def test_upload_refuses_another_projects_batch(client, db_session, test_project):
    other = Project(name="Elsewhere", slug="elsewhere")
    db_session.add(other)
    db_session.commit()
    foreign = models.ScanBatch(project_id=other.id, label="theirs")
    db_session.add(foreign)
    db_session.commit()
    assert _upload(client, test_project, NMAP_A, batch_id=foreign.id).status_code == 404


# ---------------------------------------------------------------------------
# Listing: one row per batch; the flat list can leave batch files out
# ---------------------------------------------------------------------------

def _seed_batch(db, project):
    """A 2-file nmap batch (3 distinct hosts, 2 of them new, 2 open ports),
    one queued + one failed file still in it, and a loose masscan scan."""
    now = datetime.now(timezone.utc)
    batch = models.ScanBatch(project_id=project.id, label="nmap-tcp-top1000")
    db.add(batch)
    db.commit()
    s1 = models.Scan(project_id=project.id, filename="chunk-001.xml", tool_name="nmap", batch_id=batch.id)
    s2 = models.Scan(project_id=project.id, filename="chunk-002.xml", tool_name="nmap", batch_id=batch.id)
    loose = models.Scan(project_id=project.id, filename="masscan.json", tool_name="masscan")
    s1.created_at, s2.created_at = now - timedelta(minutes=5), now
    db.add_all([s1, s2, loose])
    db.commit()
    hosts = [models.Host(project_id=project.id, ip_address=f"10.8.0.{i}", state="up") for i in (1, 2, 3)]
    db.add_all(hosts)
    db.commit()
    db.add_all([
        models.HostScanHistory(host_id=hosts[0].id, scan_id=s1.id, host_created=True),
        models.HostScanHistory(host_id=hosts[1].id, scan_id=s1.id, host_created=True),
        models.HostScanHistory(host_id=hosts[1].id, scan_id=s2.id, host_created=False),
        models.HostScanHistory(host_id=hosts[2].id, scan_id=s2.id, host_created=False),
    ])
    ports = [models.Port(host_id=hosts[i].id, port_number=22, protocol="tcp", state="open") for i in (0, 1)]
    db.add_all(ports)
    db.commit()
    db.add_all([
        models.PortScanHistory(port_id=ports[0].id, scan_id=s1.id, state_at_scan="open"),
        models.PortScanHistory(port_id=ports[1].id, scan_id=s2.id, state_at_scan="open"),
    ])
    for status in ("queued", "failed"):
        db.add(models.IngestionJob(
            project_id=project.id, filename="f.xml", original_filename="f.xml",
            storage_path="/x", status=status, batch_id=batch.id,
        ))
    db.commit()
    return batch, s1, s2, loose


def test_batches_list_one_row_with_what_the_files_added(client, db_session, test_project):
    batch, *_ = _seed_batch(db_session, test_project)
    rows = client.get(f"/api/v1/projects/{test_project.id}/scans/batches").json()
    assert len(rows) == 1
    row = rows[0]
    assert (row["id"], row["label"], row["files"]) == (batch.id, "nmap-tcp-top1000", 2)
    assert row["tools"] == ["nmap"]
    assert (row["hosts"], row["new_hosts"], row["open_ports"]) == (3, 2, 2)
    assert (row["pending_files"], row["failed_files"]) == (1, 1)

    # A filter no batch file matches leaves no batch row.
    assert client.get(f"/api/v1/projects/{test_project.id}/scans/batches?tool=masscan").json() == []


def test_flat_list_can_leave_batch_files_out_or_show_one_batch(client, db_session, test_project):
    batch, s1, s2, loose = _seed_batch(db_session, test_project)
    base = f"/api/v1/projects/{test_project.id}/scans/"

    everything = {r["id"]: r for r in client.get(base).json()}
    assert set(everything) == {s1.id, s2.id, loose.id}
    assert everything[s1.id]["batch_label"] == "nmap-tcp-top1000"
    assert everything[loose.id]["batch_id"] is None

    assert [r["id"] for r in client.get(base + "?unbatched=true").json()] == [loose.id]
    assert {r["id"] for r in client.get(base + f"?batch_id={batch.id}").json()} == {s1.id, s2.id}


def test_inventory_marker_moves_when_a_scan_lands(client, db_session, test_project):
    url = f"/api/v1/projects/{test_project.id}/scans/inventory-marker"
    before = client.get(url).json()
    assert before == {"count": 0, "latest_id": None}
    scan = models.Scan(project_id=test_project.id, filename="new.xml")
    db_session.add(scan)
    db_session.commit()
    assert client.get(url).json() == {"count": 1, "latest_id": scan.id}
