"""Ingestion attempt fencing (review 2026-09-09, Critical 2).

``started_at`` is rewritten on every claim and is the attempt's token.  A
stale attempt — reaped and re-claimed by a peer — must not renew the new
owner's heartbeat, publish its result, or fail the row.  Mirrors the
report-job service's ``started_at = :claimed`` predicate.
"""
from datetime import datetime, timedelta, timezone

import pytest


def _recent(seconds_ago: int = 30) -> datetime:
    # Claims must be recent — update_heartbeat also enforces the job timeout
    # against started_at.  Microseconds dropped so the token round-trips
    # through Postgres/SQLite identically.
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).replace(microsecond=0)

from app.db import models
from app.services.ingestion_service import IngestionService, ParseFailure


def _job(db, project_id, status="processing", started_at=None, heartbeat=None):
    job = models.IngestionJob(
        project_id=project_id,
        filename="scan.xml",
        original_filename="scan.xml",
        storage_path="/nonexistent/scan.xml",
        status=status,
        started_at=started_at,
        last_heartbeat=heartbeat,
        retry_count=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _fresh(db, job_id):
    db.expire_all()
    return db.query(models.IngestionJob).filter_by(id=job_id).one()


def test_stale_heartbeat_does_not_touch_the_new_owner(db_session, test_project):
    owner_claim = _recent(30)
    old_hb = owner_claim + timedelta(seconds=5)
    job = _job(db_session, test_project.id, started_at=owner_claim, heartbeat=old_hb)
    stale_claim = owner_claim - timedelta(minutes=10)

    with pytest.raises(ParseFailure):
        IngestionService().update_heartbeat(db_session, job.id, "50%", claimed_at=stale_claim)

    row = _fresh(db_session, job.id)
    assert row.last_heartbeat.replace(tzinfo=None) == old_hb.replace(tzinfo=None)
    assert row.progress is None
    assert row.status == "processing"


def test_matching_token_heartbeat_writes(db_session, test_project):
    claim = _recent(30)
    job = _job(db_session, test_project.id, started_at=claim, heartbeat=claim)
    IngestionService().update_heartbeat(db_session, job.id, "50%", claimed_at=claim)
    row = _fresh(db_session, job.id)
    assert row.progress == "50%"
    assert row.last_heartbeat.replace(tzinfo=None) > claim.replace(tzinfo=None)


def test_stale_completion_is_not_published(db_session, test_project, monkeypatch):
    owner_claim = _recent(30)
    job = _job(db_session, test_project.id, started_at=owner_claim, heartbeat=owner_claim)
    stale_claim = owner_claim - timedelta(minutes=10)

    svc = IngestionService()
    monkeypatch.setattr(
        svc, "_process_job",
        lambda db, j: {"scan_id": None, "tool_name": "nmap", "message": "done"},
    )
    svc._run_job(job.id, claimed_at=stale_claim)

    row = _fresh(db_session, job.id)
    assert row.status == "processing"
    assert row.completed_at is None
    assert row.started_at.replace(tzinfo=None) == owner_claim.replace(tzinfo=None)


def test_matching_completion_is_published(db_session, test_project, monkeypatch):
    claim = _recent(30)
    job = _job(db_session, test_project.id, started_at=claim, heartbeat=claim)
    svc = IngestionService()
    monkeypatch.setattr(
        svc, "_process_job",
        lambda db, j: {"scan_id": None, "tool_name": "nmap", "message": "done"},
    )
    svc._run_job(job.id, claimed_at=claim)
    row = _fresh(db_session, job.id)
    assert row.status == "completed"
    assert row.completed_at is not None


def test_stale_failure_leaves_requeued_row_queued(db_session, test_project, monkeypatch):
    # Reaper re-queued the row (started_at nulled) after this attempt went
    # quiet; the attempt then dies late.  Its failure must not land.
    job = _job(db_session, test_project.id, status="queued", started_at=None)
    stale_claim = _recent(600)

    svc = IngestionService()

    def boom(db, j):
        raise ParseFailure("bad file", user_message="Bad file")

    monkeypatch.setattr(svc, "_process_job", boom)
    svc._run_job(job.id, claimed_at=stale_claim)

    row = _fresh(db_session, job.id)
    assert row.status == "queued"
    assert row.error_message is None
    assert row.retry_count == 0

    # Same for the generic-exception path.
    def crash(db, j):
        raise RuntimeError("driver blew up")

    monkeypatch.setattr(svc, "_process_job", crash)
    svc._run_job(job.id, claimed_at=stale_claim)
    row = _fresh(db_session, job.id)
    assert row.status == "queued"
    assert row.last_error is None


def test_owning_failure_still_lands(db_session, test_project, monkeypatch):
    claim = _recent(30)
    job = _job(db_session, test_project.id, started_at=claim, heartbeat=claim)
    svc = IngestionService()

    def boom(db, j):
        raise ParseFailure("bad file", user_message="Bad file")

    monkeypatch.setattr(svc, "_process_job", boom)
    svc._run_job(job.id, claimed_at=claim)
    row = _fresh(db_session, job.id)
    assert row.status == "failed"
    assert row.error_message == "Bad file"
    assert row.message == "Bad file"
    assert row.retry_count == 1
    assert row.parse_error_id is None


def test_cancel_before_start_is_fenced_on_the_token(db_session, test_project):
    """The 'cancelled before processing started' branch goes through the same
    fenced ``fail`` as every other lifecycle write: a stale attempt must not
    fail a row a peer now owns; the owning attempt still lands the cancel."""
    owner_claim = _recent(30)
    job = _job(db_session, test_project.id, started_at=owner_claim, heartbeat=owner_claim)

    svc = IngestionService()
    svc._cancelled.add(job.id)
    try:
        # Stale token → nothing written.
        svc._run_job(job.id, owner_claim - timedelta(minutes=10))
        row = _fresh(db_session, job.id)
        assert row.status == "processing"
        assert row.error_message is None
        assert row.completed_at is None

        # Owning token → the cancel lands with the original wording.  _run_job
        # clears the in-proc cancel flag on exit, so re-arm it as cancel_job would.
        svc._cancelled.add(job.id)
        svc._run_job(job.id, owner_claim)
        row = _fresh(db_session, job.id)
        assert row.status == "failed"
        assert row.error_message == "Cancelled before processing started"
        assert row.completed_at is not None
        assert (row.retry_count or 0) == 0  # a cancel is not an attempt
    finally:
        svc._cancelled.discard(job.id)
