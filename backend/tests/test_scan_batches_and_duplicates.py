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


def test_batch_with_no_imported_file_yet_is_still_listed(client, db_session, test_project):
    """v2.350.0 — the listing starts from the batch table: a batch whose
    files are all queued or all failed has no scan row and used to be
    invisible exactly when the operator needed it."""
    batch = models.ScanBatch(project_id=test_project.id, label="all-failed")
    db_session.add(batch)
    db_session.commit()
    for status in ("failed", "failed", "processing"):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename="f.xml", original_filename="f.xml",
            storage_path="/x", status=status, batch_id=batch.id,
        ))
    db_session.commit()

    rows = client.get(f"/api/v1/projects/{test_project.id}/scans/batches").json()
    assert [r["label"] for r in rows] == ["all-failed"]
    row = rows[0]
    assert (row["files"], row["total_files"], row["imported_files"]) == (0, 0, 0)
    assert (row["processing_files"], row["failed_files"]) == (1, 2)
    assert row["last_uploaded"] is not None  # falls back to the batch's creation

    # A tool filter is about imported files: nothing matches, so it drops out.
    assert client.get(f"/api/v1/projects/{test_project.id}/scans/batches?tool=nmap").json() == []


def test_a_batch_every_file_of_which_was_refused_is_not_listed(client, db_session, test_project):
    """v2.385.0 — the page creates the batch before its files upload; when
    every file came back a duplicate, an empty "N files · nothing imported"
    row was left in the history with no reason."""
    empty = models.ScanBatch(project_id=test_project.id, label="26 files · refused")
    kept = models.ScanBatch(project_id=test_project.id, label="one staged")
    db_session.add_all([empty, kept])
    db_session.commit()
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="f.xml", original_filename="f.xml",
        storage_path="/x", status="staged", batch_id=kept.id,
    ))
    db_session.commit()

    base = f"/api/v1/projects/{test_project.id}/scans"
    assert [r["id"] for r in client.get(f"{base}/batches").json()] == [kept.id]
    history = client.get(f"{base}/history").json()
    assert [(e["kind"], e["id"]) for e in history["items"]] == [("batch", kept.id)]
    assert history["batch_total"] == 1
    # Asked for by id it is still answered: the caller named it.
    assert [r["id"] for r in client.get(f"{base}/batches", params={"ids": str(empty.id)}).json()] == [empty.id]


def _history_fixture(db, project):
    """Newest first: single s3 (t-1h) · batch B (newest file t-2h) · single s2
    (t-3h) · batch A (t-4h) · single s1 (t-5h) · batch Q (no file yet, t-6h)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    def at(hours):
        return now - timedelta(hours=hours)

    def batch(label, hours):
        b = models.ScanBatch(project_id=project.id, label=label, created_at=at(hours))
        db.add(b)
        db.flush()
        return b

    def scan(name, hours, tool="nmap", batch_id=None):
        s = models.Scan(project_id=project.id, filename=name, tool_name=tool,
                        scan_type="port_scan", created_at=at(hours), batch_id=batch_id)
        db.add(s)
        db.flush()
        return s

    a, b, q = batch("A", 4.5), batch("B", 2.5), batch("Q", 6)
    out = {
        "A": a, "B": b, "Q": q,
        "s1": scan("s1.xml", 5), "s2": scan("s2.xml", 3, tool="masscan"), "s3": scan("s3.xml", 1),
        "a1": scan("a1.xml", 4, batch_id=a.id),
        "b1": scan("b1.xml", 2.4, batch_id=b.id), "b2": scan("b2.xml", 2, tool="masscan", batch_id=b.id),
    }
    db.add(models.IngestionJob(project_id=project.id, filename="q.xml", original_filename="q.xml",
                               storage_path="/x", status="queued", batch_id=q.id))
    db.commit()
    return out


def test_import_history_interleaves_batches_and_single_files_by_upload_time(client, db_session, test_project):
    """The page showed batches and single files as two separately paginated
    cards, so the order things were imported in could not be read off it."""
    f = _history_fixture(db_session, test_project)
    url = f"/api/v1/projects/{test_project.id}/scans/history"

    body = client.get(url).json()
    assert [(e["kind"], e["id"]) for e in body["items"]] == [
        ("scan", f["s3"].id), ("batch", f["B"].id), ("scan", f["s2"].id),
        ("batch", f["A"].id), ("scan", f["s1"].id), ("batch", f["Q"].id),
    ]
    assert (body["total"], body["batch_total"], body["scan_total"], body["has_more"]) == (6, 3, 3, False)
    # A batch's files are its own rows' business, never history rows.
    assert f["b1"].id not in [e["id"] for e in body["items"] if e["kind"] == "scan"]

    # Pagination runs over the MERGED order — the thing two lists could not do.
    first = client.get(url, params={"limit": 2}).json()
    second = client.get(url, params={"skip": 2, "limit": 2}).json()
    third = client.get(url, params={"skip": 4, "limit": 2}).json()
    paged = [(e["kind"], e["id"]) for page in (first, second, third) for e in page["items"]]
    assert paged == [(e["kind"], e["id"]) for e in body["items"]]
    assert (first["has_more"], second["has_more"], third["has_more"]) == (True, True, False)


def test_import_history_filters_like_the_lists_it_orders(client, db_session, test_project):
    f = _history_fixture(db_session, test_project)
    body = client.get(f"/api/v1/projects/{test_project.id}/scans/history", params={"tool": "masscan"}).json()
    # Batch B holds a masscan file; A and the still-queued Q do not match.
    assert [(e["kind"], e["id"]) for e in body["items"]] == [("batch", f["B"].id), ("scan", f["s2"].id)]
    assert (body["batch_total"], body["scan_total"]) == (1, 1)


def test_batches_can_be_fetched_by_id_for_a_history_page(client, db_session, test_project):
    f = _history_fixture(db_session, test_project)
    url = f"/api/v1/projects/{test_project.id}/scans/batches"
    rows = client.get(url, params={"ids": f"{f['A'].id},{f['Q'].id}"}).json()
    assert sorted(r["label"] for r in rows) == ["A", "Q"]
    assert client.get(url, params={"ids": "x"}).status_code == 422
    assert client.get(url, params={"ids": ""}).json() == []


def test_an_operator_upload_can_be_named_an_agents_batch_cannot(client, db_session, test_project):
    url = f"/api/v1/projects/{test_project.id}/scans/batches"
    created = client.post(url, json={"label": "12 files · today"}).json()
    r = client.patch(f"{url}/{created['id']}", json={"label": "  DMZ sweep, week 2  "})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "DMZ sweep, week 2"
    assert client.patch(f"{url}/{created['id']}", json={"label": "   "}).status_code == 422
    assert client.patch(f"{url}/999999", json={"label": "x"}).status_code == 404

    # An agent's batch is keyed by (recon session, label): a rename would send
    # the sweep's next chunk into a new batch.
    scope = models.Scope(project_id=test_project.id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    from app.db.models_agent import ReconSession
    session = ReconSession(project_id=test_project.id, scope_id=scope.id, status="active")
    db_session.add(session)
    db_session.flush()
    agent_batch = models.ScanBatch(project_id=test_project.id, label="nmap-tcp-top1000", recon_session_id=session.id)
    db_session.add(agent_batch)
    db_session.commit()
    assert client.patch(f"{url}/{agent_batch.id}", json={"label": "renamed"}).status_code == 409
    db_session.refresh(agent_batch)
    assert agent_batch.label == "nmap-tcp-top1000"


def test_batch_says_where_its_staged_and_discarded_files_are(client, db_session, test_project):
    """A staged file is neither processing nor failed, so a freshly dropped
    batch read "0 imported" with nothing explaining where its files were; a
    discarded one is a dismissed failure and vanished from the counts too."""
    from datetime import datetime, timezone

    batch = models.ScanBatch(project_id=test_project.id, label="awaiting-review")
    db_session.add(batch)
    db_session.commit()
    now = datetime.now(timezone.utc)
    for status, error, dismissed in (
        ("staged", None, None),
        ("staged", None, None),
        ("failed", "Discarded before import", now),   # discarded
        ("failed", "parser blew up", now),            # dismissed failure: uncounted, as before
        ("failed", "parser blew up", None),           # live failure
    ):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename="f.xml", original_filename="f.xml",
            storage_path="/x", status=status, batch_id=batch.id,
            error_message=error, dismissed_at=dismissed,
        ))
    db_session.commit()

    row = client.get(f"/api/v1/projects/{test_project.id}/scans/batches").json()[0]
    assert row["staged_files"] == 2
    assert row["discarded_files"] == 1
    assert (row["processing_files"], row["failed_files"], row["imported_files"]) == (0, 1, 0)
    assert (row["expired_files"], row["dismissed_failed_files"]) == (0, 1)


def test_batch_with_nothing_imported_says_why_and_counts_reprocessed_files(
    client, db_session, test_project, test_user,
):
    """v2.401.0 — a batch whose 31 staged files expired (dismissed) read
    "0 files · nothing imported"; and a re-processed file joins its
    original's batch, so "31 files" held 32 scans with nothing saying why."""
    from datetime import datetime, timezone

    test_user.full_name = "Ana Analyst"
    expired = models.ScanBatch(project_id=test_project.id, label="31 files · a", created_by_id=test_user.id)
    reproc = models.ScanBatch(project_id=test_project.id, label="2 files · b", created_by_id=test_user.id)
    db_session.add_all([expired, reproc])
    db_session.commit()
    now = datetime.now(timezone.utc)
    msg = "Staged upload expired: not started within 24 hours."
    for dismissed in (now, None):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename="f.xml", original_filename="f.xml", storage_path="/x",
            status="failed", batch_id=expired.id, error_message=msg, dismissed_at=dismissed,
        ))
    for i, options in enumerate(({}, {}, {"reprocess_of_job_id": 1})):
        scan = models.Scan(project_id=test_project.id, filename=f"r{i}.xml", tool_name="nmap", batch_id=reproc.id)
        db_session.add(scan)
        db_session.flush()
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename="r.xml", original_filename="r.xml", storage_path="/x",
            status="completed", batch_id=reproc.id, scan_id=scan.id, options=options,
        ))
    db_session.commit()

    rows = {r["id"]: r for r in client.get(f"/api/v1/projects/{test_project.id}/scans/batches").json()}
    e = rows[expired.id]
    # Both expiries — dismissed or not — are expiries, not live failures.
    assert (e["expired_files"], e["failed_files"], e["imported_files"]) == (2, 0, 0)
    r = rows[reproc.id]
    assert (r["imported_files"], r["reprocessed_files"]) == (3, 1)
    assert (r["created_by"], r["created_by_name"]) == (test_user.username, "Ana Analyst")


def test_summary_counts_failed_imports_across_the_whole_project(client, db_session, test_project):
    """v2.401.0 — the lead read the 25 most recent jobs and said "nothing
    failed" while older failures existed.  The summary carries the project's
    needs-attention count (failed or partial, not dismissed) and the failures
    already dismissed (discards and expiries included)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for status, partial, dismissed, error in (
        ("failed", False, None, "bad xml"),
        ("completed", True, None, None),                    # partial: needs attention
        ("failed", False, now, "Discarded before import"),
        ("failed", False, now, "Staged upload expired: not started within 24 hours."),
        ("completed", False, None, None),
    ) + tuple(("completed", False, None, None) for _ in range(30)):
        db_session.add(models.IngestionJob(
            project_id=test_project.id, filename="f", original_filename="f", storage_path="/x",
            status=status, partial=partial, dismissed_at=dismissed, error_message=error,
        ))
    db_session.commit()
    s = client.get(f"/api/v1/projects/{test_project.id}/scans/summary").json()
    assert (s["imports_need_attention"], s["imports_not_imported"]) == (2, 2)


def test_filtered_batch_reports_matching_of_total_files(client, db_session, test_project):
    batch = models.ScanBatch(project_id=test_project.id, label="mixed")
    db_session.add(batch)
    db_session.commit()
    db_session.add_all([
        models.Scan(project_id=test_project.id, filename="a.xml", tool_name="nmap", batch_id=batch.id),
        models.Scan(project_id=test_project.id, filename="b.xml", tool_name="nmap", batch_id=batch.id),
        models.Scan(project_id=test_project.id, filename="c.json", tool_name="masscan", batch_id=batch.id),
    ])
    db_session.commit()
    (row,) = client.get(f"/api/v1/projects/{test_project.id}/scans/batches?tool=masscan").json()
    assert (row["files"], row["total_files"], row["imported_files"]) == (1, 3, 3)
    assert row["tools"] == ["masscan"]


def test_summary_tool_counts_cover_batched_files_and_ignore_the_tool_filter(client, db_session, test_project):
    """v2.350.0 — the chips used to count the loaded rows, which in grouped
    mode were only the unbatched files."""
    _seed_batch(db_session, test_project)  # 2 nmap files in a batch + 1 loose masscan
    s = client.get(f"/api/v1/projects/{test_project.id}/scans/summary").json()
    assert s["tool_counts"] == {"NMAP": 2, "MASSCAN": 1}
    assert s["total_files"] == 3
    filtered = client.get(f"/api/v1/projects/{test_project.id}/scans/summary?tool=masscan").json()
    assert filtered["tool_counts"] == {"NMAP": 2, "MASSCAN": 1}
    assert filtered["total_scans"] == 1


def test_scans_filter_by_who_uploaded_them(client, db_session, test_project, test_user):
    """v2.396.0 — every /scans list (flat, history, batches, summary) takes
    ``uploaded_by``; the summary names the uploaders for the chooser and is
    not narrowed by that filter itself."""
    from app.db.models_auth import User, UserRole

    other = User(
        id=501, username="ben", full_name="Ben Tester", email="ben@example.com",
        hashed_password="x", role=UserRole.MEMBER,
    )
    db_session.add(other)
    db_session.commit()
    f = _history_fixture(db_session, test_project)
    # test_user uploaded s1 and batch B's masscan file; ben uploaded s3; the rest nobody recorded.
    f["s1"].uploaded_by_id = f["b2"].uploaded_by_id = test_user.id
    f["s3"].uploaded_by_id = other.id
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/scans"

    mine = {"uploaded_by": test_user.id}
    assert {r["id"] for r in client.get(f"{base}/", params=mine).json()} == {f["s1"].id, f["b2"].id}
    history = client.get(f"{base}/history", params=mine).json()
    assert [(e["kind"], e["id"]) for e in history["items"]] == [("batch", f["B"].id), ("scan", f["s1"].id)]
    batches = client.get(f"{base}/batches", params=mine).json()
    assert [(b["id"], b["files"], b["total_files"]) for b in batches] == [(f["B"].id, 1, 2)]

    summary = client.get(f"{base}/summary", params=mine).json()
    assert summary["total_scans"] == 2
    assert summary["tool_counts"] == {"NMAP": 1, "MASSCAN": 1}
    assert summary["uploaders"] == [
        {"user_id": test_user.id, "username": test_user.username, "full_name": test_user.full_name, "files": 2},
        # v2.401.0 — the chooser displays the full name; the id stays the value.
        {"user_id": other.id, "username": "ben", "full_name": "Ben Tester", "files": 1},
    ]
    listed = {r["id"]: r for r in client.get(f"{base}/").json()}
    assert (listed[f["s3"].id]["uploaded_by"], listed[f["s3"].id]["uploaded_by_name"]) == ("ben", "Ben Tester")
    # The chooser follows the other filters.
    by_tool = client.get(f"{base}/summary", params={"tool": "masscan"}).json()
    assert [u["username"] for u in by_tool["uploaders"]] == [test_user.username]
    assert client.get(f"{base}/", params={"uploaded_by": 0}).status_code == 422


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
