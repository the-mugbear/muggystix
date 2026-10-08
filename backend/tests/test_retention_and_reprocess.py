"""Retention and explicit re-process (v2.354.0; staged-import phase E).

Pins:

* a finished job's file stays on disk (nothing removed it before either —
  the "only successful parses unlink it" docstring was stale) and the
  Ingestion Results row says until when;
* the retention sweep removes finished jobs' files past the window and
  leaves recent ones and open jobs alone; the job rows stay;
* re-process creates a NEW queued job over a copy of the bytes, with the
  chosen format and source tool, bypassing the duplicate guard, and
  refuses an unfinished job, an unknown format, or a job whose file is gone.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import models
from app.services.staged_import_service import expire_retained_files, retained_until

NMAP_XML = b'<?xml version="1.0"?>\n<nmaprun scanner="nmap"><host><status state="up"/><address addr="10.3.0.1" addrtype="ipv4"/></host></nmaprun>\n'


def _upload(client, project, data, name, **form):
    return client.post(
        f"/api/v1/projects/{project.id}/upload/",
        files={"file": (name, data, "text/xml")},
        data={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()},
    )


def _finish(db, job_id, status="completed", when=None):
    job = db.get(models.IngestionJob, job_id)
    job.status = status
    job.completed_at = when or datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def test_finished_job_file_is_retained_and_the_row_says_until_when(client, db_session, test_project):
    job_id = _upload(client, test_project, NMAP_XML, "a.xml", stage=True).json()["job_id"]
    job = _finish(db_session, job_id)
    assert Path(job.storage_path).exists()
    until = retained_until(job)
    assert until is not None and until > datetime.now(timezone.utc) + timedelta(days=6)

    rows = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results").json()["items"]
    row = next(r for r in rows if r["id"] == job_id)
    assert row["file_retained"] is True and row["retained_until"] is not None


def test_retention_sweep_removes_old_finished_files_only(client, db_session, test_project):
    old_id = _upload(client, test_project, NMAP_XML, "old.xml", stage=True).json()["job_id"]
    fresh_id = _upload(client, test_project, NMAP_XML, "fresh.xml", stage=True, allow_duplicate=True).json()["job_id"]
    open_id = _upload(client, test_project, NMAP_XML, "open.xml", stage=True, allow_duplicate=True).json()["job_id"]
    old = _finish(db_session, old_id, "failed", datetime.now(timezone.utc) - timedelta(days=9))
    fresh = _finish(db_session, fresh_id)
    open_job = db_session.get(models.IngestionJob, open_id)  # still staged

    assert expire_retained_files(db_session) == 1
    assert not Path(old.storage_path).exists()
    assert Path(fresh.storage_path).exists()
    assert Path(open_job.storage_path).exists()
    db_session.refresh(old)
    assert old.status == "failed"  # the record stays; only the bytes went
    assert retained_until(old) is None


def test_the_sweep_looks_at_a_finished_job_once_not_every_minute(client, db_session, test_project):
    """Review 2026-10-07: the rows stay for ever, so every sweep locked and
    committed once per job the instance had ever finished."""
    from sqlalchemy import event

    job_id = _upload(client, test_project, NMAP_XML, "seen.xml", stage=True).json()["job_id"]
    _finish(db_session, job_id, "completed", datetime.now(timezone.utc) - timedelta(days=9))
    assert expire_retained_files(db_session) == 1

    locked = []

    def count_row_locks(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement.upper():
            locked.append(statement)

    engine = db_session.get_bind().engine
    event.listen(engine, "before_cursor_execute", count_row_locks)
    try:
        assert expire_retained_files(db_session) == 0
    finally:
        event.remove(engine, "before_cursor_execute", count_row_locks)
    assert locked == []


def test_a_file_the_sweep_cannot_remove_is_said_and_tried_again(client, db_session, test_project, monkeypatch):
    import logging

    from app.services import staged_import_service

    job_id = _upload(client, test_project, NMAP_XML, "stuck.xml", stage=True).json()["job_id"]
    job = _finish(db_session, job_id, "completed", datetime.now(timezone.utc) - timedelta(days=9))
    warnings = []

    class _Capture(logging.Handler):  # ``app`` loggers do not reach caplog's root handler
        def emit(self, record):
            warnings.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    staged_import_service.logger.addHandler(handler)
    real_rmtree = staged_import_service.shutil.rmtree
    monkeypatch.setattr(staged_import_service.shutil, "rmtree", lambda *a, **k: None)
    try:
        assert expire_retained_files(db_session) == 0
    finally:
        staged_import_service.logger.removeHandler(handler)
    assert any("Could not remove the retained file" in w for w in warnings)
    monkeypatch.setattr(staged_import_service.shutil, "rmtree", real_rmtree)
    assert expire_retained_files(db_session) == 1
    assert not Path(job.storage_path).exists()


def test_a_retry_accepted_after_the_sweep_read_keeps_its_file(client, db_session, test_project):
    """2.374.4 review H5: the sweep deleted from a snapshot read, so a retry
    accepted between its read and its delete was queued with no input file.
    The retry's write is injected right after the sweep's first read."""
    from sqlalchemy import event, update

    job_id = _upload(client, test_project, NMAP_XML, "r.xml", stage=True).json()["job_id"]
    job = _finish(db_session, job_id, "failed", datetime.now(timezone.utc) - timedelta(days=9))
    path = Path(job.storage_path)
    fired = []

    def retry_lands_after_the_read(state):
        if fired or not state.is_select:
            return None
        fired.append(True)
        result = state.invoke_statement()
        state.session.connection().execute(
            update(models.IngestionJob).where(models.IngestionJob.id == job_id)
            .values(status="queued", completed_at=None)
        )
        return result

    event.listen(db_session, "do_orm_execute", retry_lands_after_the_read)
    try:
        expire_retained_files(db_session)
    finally:
        event.remove(db_session, "do_orm_execute", retry_lands_after_the_read)
    assert fired
    assert path.exists()


def test_reprocess_makes_a_new_queued_job_over_a_copy(client, db_session, test_project):
    job_id = _upload(client, test_project, NMAP_XML, "a.xml", stage=True).json()["job_id"]
    src = _finish(db_session, job_id)

    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/reprocess",
        json={"format_override": "masscan_xml", "source_tool": "masscan 1.3"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] != job_id
    assert body["status"] == "queued"
    assert body["format_override"] == "masscan_xml"
    assert body["source_tool"] == "masscan 1.3"
    new = db_session.get(models.IngestionJob, body["id"])
    assert new.options["reprocess_of_job_id"] == job_id
    assert new.content_sha256 == src.content_sha256  # same bytes, guard bypassed
    assert Path(new.storage_path).exists() and new.storage_path != src.storage_path
    assert Path(src.storage_path).exists()  # the original keeps its own copy

    # Refusals: unfinished, unknown format, file gone.
    assert client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{new.id}/reprocess", json={}).status_code == 409
    assert client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/reprocess", json={"format_override": "nope"},
    ).status_code == 422
    Path(src.storage_path).unlink()
    assert client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/reprocess", json={}).status_code == 409
