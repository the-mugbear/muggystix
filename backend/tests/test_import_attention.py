"""Superseded failures and specific failure reasons (v2.403.0).

Project "Local Network" read "4 imports need attention" on /scans and
Ingestion Results while each of the four files had since been imported by a
later job (same project, same sha256).  A failed/partial job whose file a
LATER job imported cleanly is SUPERSEDED: out of "needs attention" everywhere
(one definition, ``blocked_import_condition``), naming the job that imported
it, and dismissable in bulk.

And every failed row said "Failed to parse the file 'x'. The file format may
not be supported or the file may be corrupted." while the parser's own cause
("SMBMap parser found 0 hosts in x") sat on the ParseError.
"""
from datetime import datetime, timezone

from app.db import models
from app.services.operations_read_service import compute_blockers

GENERIC = (
    "Failed to parse the file 'smbmap-samba.txt'. The file format may not be "
    "supported or the file may be corrupted."
)


def _job(db, project, *, sha, status="failed", partial=False, dismissed=None, name="f.txt",
         batch=None, error=None, parse_error=None):
    job = models.IngestionJob(
        project_id=project.id, filename=name, original_filename=name, storage_path="/x",
        status=status, partial=partial, dismissed_at=dismissed, content_sha256=sha,
        batch_id=batch, error_message=error, parse_error_id=parse_error,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _other_project(db):
    from app.db.models_project import Project
    p = Project(name="other", slug="other", description="x")
    db.add(p)
    db.commit()
    return p


def _scenario(db, project):
    """A: failed, later imported cleanly → superseded.
    C: failed, never imported again → needs attention.
    D: failed, later job was only PARTIAL → still needs attention.
    E: failed, the same file imported cleanly in ANOTHER project → still needs attention.
    F: completed partial, later imported cleanly → superseded.
    G: failed AFTER a clean import of the same file (not later) → needs attention."""
    other = _other_project(db)
    a = _job(db, project, sha="a" * 64, name="smbmap-samba.txt")
    c = _job(db, project, sha="c" * 64)
    d = _job(db, project, sha="d" * 64)
    e = _job(db, project, sha="e" * 64)
    f = _job(db, project, sha="f" * 64, status="completed", partial=True)
    g_clean = _job(db, project, sha="9" * 64, status="completed")
    g = _job(db, project, sha="9" * 64)
    b = _job(db, project, sha="a" * 64, status="completed", name="smbmap-samba.txt")
    # The partial re-import of D needs attention itself (nothing later is clean).
    d_partial = _job(db, project, sha="d" * 64, status="completed", partial=True)
    _job(db, other, sha="e" * 64, status="completed")
    _job(db, project, sha="f" * 64, status="completed")
    return {"a": a, "b": b, "c": c, "d": d, "d_partial": d_partial, "e": e, "f": f, "g": g, "g_clean": g_clean}


def test_superseded_failures_leave_needs_attention_everywhere(client, db_session, test_project):
    j = _scenario(db_session, test_project)
    base = f"/api/v1/projects/{test_project.id}"
    attention = {j[k].id for k in ("c", "d", "d_partial", "e", "g")}

    s = client.get(f"{base}/scans/summary").json()
    assert s["imports_need_attention"] == len(attention)
    assert s["imports_superseded"] == 2

    r = client.get(f"{base}/parse-errors/ingestion-results").json()
    assert r["summary"]["total_needs_attention"] == len(attention)
    assert r["summary"]["total_superseded"] == 2

    listed = client.get(f"{base}/parse-errors/ingestion-results", params={"status": "needs_attention"}).json()
    assert {i["id"] for i in listed["items"]} == attention
    assert listed["total"] == len(attention)

    sup = client.get(f"{base}/parse-errors/ingestion-results", params={"status": "superseded"}).json()
    rows = {i["id"]: i for i in sup["items"]}
    assert set(rows) == {j["a"].id, j["f"].id}
    assert rows[j["a"].id]["superseded_by_job_id"] == j["b"].id

    blockers = compute_blockers(db_session, test_project)
    assert blockers.failed_import_count + blockers.partial_import_count == len(attention)


def test_job_lists_say_which_job_imported_the_file(client, db_session, test_project):
    j = _scenario(db_session, test_project)
    jobs = {x["id"]: x for x in client.get(f"/api/v1/projects/{test_project.id}/upload/jobs?limit=100").json()}
    assert jobs[j["a"].id]["superseded_by_job_id"] == j["b"].id
    assert jobs[j["f"].id]["superseded_by_job_id"] is not None
    for key in ("c", "d", "e", "g"):
        assert jobs[j[key].id]["superseded_by_job_id"] is None, key


def test_dismiss_superseded_dismisses_only_superseded_named_jobs(client, db_session, test_project):
    j = _scenario(db_session, test_project)
    base = f"/api/v1/projects/{test_project.id}"
    # C is named but not superseded: skipped, not dismissed.
    resp = client.post(f"{base}/upload/jobs/dismiss-superseded", json={"job_ids": [j["a"].id, j["c"].id]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dismissed": 1, "job_ids": [j["a"].id]}
    db_session.expire_all()
    assert db_session.get(models.IngestionJob, j["a"].id).dismissed_at is not None
    assert db_session.get(models.IngestionJob, j["c"].id).dismissed_at is None
    # F was not named, so it is still superseded and undismissed.
    s = client.get(f"{base}/scans/summary").json()
    assert s["imports_superseded"] == 1


def test_single_dismiss_goes_through_the_transition_rules(client, db_session, test_project):
    j = _scenario(db_session, test_project)
    base = f"/api/v1/projects/{test_project.id}/upload/jobs"
    ok = client.post(f"{base}/{j['c'].id}/dismiss")
    assert ok.status_code == 200, ok.text
    assert ok.json()["dismissed_at"] is not None
    # A clean completed job has nothing to dismiss.
    refused = client.post(f"{base}/{j['b'].id}/dismiss")
    assert refused.status_code == 400


def test_rows_carry_the_parsers_specific_reason(client, db_session, test_project):
    pe = models.ParseError(
        project_id=test_project.id, filename="smbmap-samba.txt", error_type="parsing_error",
        error_message="SMBMap parser found 0 hosts in smbmap-samba.txt; file is empty or not smbmap output.",
        user_message=GENERIC,
    )
    driver = models.ParseError(
        project_id=test_project.id, filename="nikto-all.txt", error_type="parsing_error",
        error_message=(
            "(psycopg2.errors.StringDataRightTruncation) value too long for type character varying(200)\n\n"
            "[SQL: INSERT INTO vulnerabilities (...)]"
        ),
        user_message=GENERIC,
    )
    db_session.add_all([pe, driver])
    db_session.commit()
    specific = _job(db_session, test_project, sha=None, error=GENERIC, parse_error=pe.id)
    sql = _job(db_session, test_project, sha=None, error=GENERIC, parse_error=driver.id)
    plain = _job(db_session, test_project, sha=None, error="Discarded before import",
                 dismissed=datetime.now(timezone.utc))

    base = f"/api/v1/projects/{test_project.id}"
    items = {i["id"]: i for i in client.get(f"{base}/parse-errors/ingestion-results").json()["items"]}
    assert items[specific.id]["failure_reason"].startswith("SMBMap parser found 0 hosts")
    # A driver error is a parser bug: said in plain words, never the SQL text.
    assert items[sql.id]["failure_reason"].startswith("A value in this file was longer than BlueStick stores")
    assert "character varying" not in items[sql.id]["failure_reason"]
    assert items[plain.id]["failure_reason"] == "Discarded before import"

    jobs = {x["id"]: x for x in client.get(f"{base}/upload/jobs?include_dismissed=true").json()}
    assert jobs[specific.id]["failure_reason"].startswith("SMBMap parser found 0 hosts")


def test_batch_lists_its_files_that_did_not_import_and_counts_superseded(client, db_session, test_project):
    batch = models.ScanBatch(project_id=test_project.id, label="4 files · x")
    db_session.add(batch)
    db_session.commit()
    now = datetime.now(timezone.utc)
    sup = _job(db_session, test_project, sha="1" * 64, batch=batch.id, name="a.txt", error="bad")
    live = _job(db_session, test_project, sha="2" * 64, batch=batch.id, name="b.txt", error="bad")
    disc = _job(db_session, test_project, sha="3" * 64, batch=batch.id, name="c.txt",
                error="Discarded before import", dismissed=now)
    scan = models.Scan(project_id=test_project.id, filename="d.txt", tool_name="nmap", batch_id=batch.id)
    db_session.add(scan)
    db_session.commit()
    done = _job(db_session, test_project, sha="4" * 64, batch=batch.id, name="d.txt", status="completed")
    later = _job(db_session, test_project, sha="1" * 64, status="completed", name="a.txt")

    base = f"/api/v1/projects/{test_project.id}"
    rows = client.get(f"{base}/upload/jobs", params={"batch_id": batch.id}).json()
    assert [r["id"] for r in rows] == [sup.id, live.id, disc.id]
    assert done.id not in {r["id"] for r in rows}
    by_id = {r["id"]: r for r in rows}
    assert by_id[sup.id]["superseded_by_job_id"] == later.id
    assert by_id[disc.id]["failure_reason"] == "Discarded before import"

    row = next(b for b in client.get(f"{base}/scans/batches").json() if b["id"] == batch.id)
    assert (row["failed_files"], row["superseded_files"], row["discarded_files"]) == (1, 1, 1)


def test_expired_and_discarded_uploads_are_not_counted_as_failed(client, db_session, test_project):
    """UX review 2026-09-24: 31 staged uploads that expired read "Failed 31" on
    Ingestion Results while /scans said "expired before review"."""
    from app.services.staged_import_service import DISCARDED_MESSAGE, EXPIRED_MESSAGE_PREFIX

    base = f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results"
    real = _job(db_session, test_project, sha="1" * 64, error="boom")
    no_message = _job(db_session, test_project, sha="2" * 64)
    expired = _job(db_session, test_project, sha="3" * 64,
                   error=f"{EXPIRED_MESSAGE_PREFIX}: not started within 24 hours.")
    discarded = _job(db_session, test_project, sha="4" * 64, error=DISCARDED_MESSAGE)

    summary = client.get(base).json()["summary"]
    assert (summary["total_failed"], summary["total_expired"], summary["total_discarded"]) == (2, 1, 1)

    def ids(status):
        return {i["id"] for i in client.get(base, params={"status": status}).json()["items"]}

    assert ids("failed") == {real.id, no_message.id}
    assert ids("expired") == {expired.id}
    assert ids("discarded") == {discarded.id}
