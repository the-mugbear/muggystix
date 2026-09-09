"""The shared job-transition layer (v2.328.0), exercised directly against
both queue tables.

One implementation of claim / heartbeat / complete / fail / cancel / retry /
reap for ``ingestion_jobs`` and ``report_jobs``.  These tests pin the rules
the services rely on: the claim token fences every processing-state write,
operator transitions are locked read-check-writes, and the reaper re-checks
staleness under the lock and leaves a live lease alone.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.services.job_transitions import JobNotTransitionable, JobTransitions


def _t(seconds_ago: int) -> datetime:
    # Microseconds dropped so the token round-trips identically on Postgres
    # (timestamptz) and SQLite (text).
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).replace(microsecond=0)


def _naive(d):
    return d.replace(tzinfo=None) if d is not None else None


def _ingestion(db, project_id, status="queued", **over):
    over.setdefault("retry_count", 0)
    job = models.IngestionJob(
        project_id=project_id, filename="s.xml", original_filename="s.xml",
        storage_path="/nonexistent/s.xml", status=status, **over,
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def _report(db, project_id, status="queued", **over):
    over.setdefault("retry_count", 0)
    job = models.ReportJob(
        project_id=project_id, format="json", report_type="comprehensive",
        filters={}, status=status, **over,
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


QUEUES = [
    pytest.param(models.IngestionJob, _ingestion, id="ingestion"),
    pytest.param(models.ReportJob, _report, id="report"),
]


def _fresh(db, model, job_id):
    db.expire_all()
    return db.get(model, job_id)


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model,make", QUEUES)
def test_claim_takes_the_oldest_queued_row_and_a_second_claim_skips_it(db_session, test_project, model, make):
    older = make(db_session, test_project.id, created_at=_t(120))
    newer = make(db_session, test_project.id, created_at=_t(60))
    tx = JobTransitions(model, name="t")

    claimed = tx.claim_oldest_queued(db_session, message="working")
    db_session.commit()
    assert claimed is not None
    job_id, token = claimed
    assert job_id == older.id
    row = _fresh(db_session, model, older.id)
    assert row.status == "processing"
    assert _naive(row.started_at) == _naive(token)
    assert _naive(row.last_heartbeat) == _naive(token)
    assert row.message == "working"

    # The claimed row is no longer 'queued', so the next claim moves on.
    second = tx.claim_oldest_queued(db_session)
    db_session.commit()
    assert second is not None and second[0] == newer.id

    # Queue drained.
    assert tx.claim_oldest_queued(db_session) is None


# ---------------------------------------------------------------------------
# fenced writes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model,make", QUEUES)
def test_wrong_token_affects_zero_rows(db_session, test_project, model, make):
    owner = _t(30)
    job = make(db_session, test_project.id, status="processing", started_at=owner, last_heartbeat=owner)
    tx = JobTransitions(model, name="t")
    stale = owner - timedelta(minutes=10)

    assert tx.heartbeat(db_session, job.id, stale) == 0
    assert tx.complete(db_session, job.id, stale) == 0
    assert tx.fail(db_session, job.id, stale, error_message="late") == 0
    db_session.commit()

    row = _fresh(db_session, model, job.id)
    assert row.status == "processing"
    assert _naive(row.last_heartbeat) == _naive(owner)
    assert row.completed_at is None
    assert row.error_message is None


@pytest.mark.parametrize("model,make", QUEUES)
def test_matching_token_writes_land(db_session, test_project, model, make):
    owner = _t(30)
    tx = JobTransitions(model, name="t")

    hb = make(db_session, test_project.id, status="processing", started_at=owner, last_heartbeat=owner)
    assert tx.heartbeat(db_session, hb.id, owner) == 1
    db_session.commit()
    assert _naive(_fresh(db_session, model, hb.id).last_heartbeat) > _naive(owner)

    done = make(db_session, test_project.id, status="processing", started_at=owner, last_heartbeat=owner)
    assert tx.complete(db_session, done.id, owner, message="ok") == 1
    db_session.commit()
    row = _fresh(db_session, model, done.id)
    assert row.status == "completed" and row.completed_at is not None and row.message == "ok"

    bad = make(db_session, test_project.id, status="processing", started_at=owner, last_heartbeat=owner)
    assert tx.fail(db_session, bad.id, owner, increment_retry=True, error_message="boom", last_error="boom") == 1
    db_session.commit()
    row = _fresh(db_session, model, bad.id)
    assert row.status == "failed" and row.error_message == "boom" and row.retry_count == 1


@pytest.mark.parametrize("model,make", QUEUES)
def test_fenced_writes_never_touch_a_non_processing_row(db_session, test_project, model, make):
    # Even with no token, a completion/failure must not resurrect a queued or
    # terminal row (that is the 'status = processing' half of the fence).
    tx = JobTransitions(model, name="t")
    for status in ("queued", "completed", "failed"):
        job = make(db_session, test_project.id, status=status)
        assert tx.complete(db_session, job.id, None) == 0
        assert tx.fail(db_session, job.id, None, error_message="x") == 0
        assert tx.heartbeat(db_session, job.id, None) == 0
    db_session.commit()


# ---------------------------------------------------------------------------
# operator transitions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model,make", QUEUES)
def test_cancel_of_a_processing_row_is_refused(db_session, test_project, model, make):
    job = make(db_session, test_project.id, status="processing", started_at=_t(5))
    tx = JobTransitions(model, name="t")
    with pytest.raises(JobNotTransitionable) as exc:
        tx.cancel(db_session, job.id, allowed_from=("queued",), to_status="cancelled")
    assert exc.value.status == "processing"
    db_session.rollback()
    assert _fresh(db_session, model, job.id).status == "processing"


@pytest.mark.parametrize("model,make", QUEUES)
def test_cancel_moves_a_queued_row_and_returns_none_when_unmatched(db_session, test_project, model, make):
    job = make(db_session, test_project.id, status="queued")
    tx = JobTransitions(model, name="t")
    out = tx.cancel(db_session, job.id, allowed_from=("queued",), to_status="cancelled", message="stop")
    db_session.commit()
    assert out is not None and out.status == "cancelled" and out.completed_at is not None
    assert out.message == "stop"
    # A project scope that doesn't match yields None, never a foreign row.
    assert tx.cancel(
        db_session, job.id, extra_conds=(model.project_id == test_project.id + 999,),
    ) is None
    assert tx.cancel(db_session, 987654321) is None


@pytest.mark.parametrize("model,make", QUEUES)
def test_retry_requeues_only_failed_rows_and_clears_the_token(db_session, test_project, model, make):
    tx = JobTransitions(model, name="t")
    job = make(
        db_session, test_project.id, status="failed", started_at=_t(600),
        completed_at=_t(500), error_message="e", last_error="e",
    )
    out = tx.retry(db_session, job.id, increment_retry=True, message="again")
    db_session.commit()
    row = _fresh(db_session, model, job.id)
    assert row.status == "queued"
    assert row.started_at is None and row.last_heartbeat is None and row.completed_at is None
    assert row.error_message is None and row.last_error is None
    assert row.retry_count == 1 and row.message == "again"

    with pytest.raises(JobNotTransitionable):
        tx.retry(db_session, job.id)  # now queued, not failed
    db_session.rollback()

    # A precondition reason surfaces as the exception's status.
    dead = make(db_session, test_project.id, status="failed")
    with pytest.raises(JobNotTransitionable) as exc:
        tx.retry(db_session, dead.id, precondition=lambda j: "file_missing")
    assert exc.value.status == "file_missing"
    db_session.rollback()
    assert _fresh(db_session, model, dead.id).status == "failed"


# ---------------------------------------------------------------------------
# reaper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model,make", QUEUES)
def test_reaper_requeues_stale_fails_over_budget_and_leaves_live_leases_alone(db_session, test_project, model, make):
    tx = JobTransitions(model, name="t")
    cutoff = _t(300)
    stale = _t(900)
    live = _t(10)

    fresh_row = make(db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=live)
    first_stall = make(db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=stale)
    never_beat = make(db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=None)
    exhausted = make(
        db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=stale, retry_count=2,
    )
    queued = make(db_session, test_project.id, status="queued")

    seen = []

    def decide(job):
        seen.append(job.id)
        if job.retry_count <= 2:
            return "requeue", {"message": f"back in line {job.retry_count}"}
        return "fail", {"error_message": "gave up", "last_error": "gave up"}

    result = tx.requeue_or_fail_stale(db_session, cutoff=cutoff, max_retries=2, decide=decide)
    db_session.commit()

    assert sorted(result.requeued) == sorted([first_stall.id, never_beat.id])
    assert result.failed == [exhausted.id]
    assert result.total == 3
    assert fresh_row.id not in seen and queued.id not in seen

    r = _fresh(db_session, model, first_stall.id)
    assert r.status == "queued" and r.retry_count == 1 and r.started_at is None and r.last_heartbeat is None
    assert r.message == "back in line 1"
    r = _fresh(db_session, model, never_beat.id)
    assert r.status == "queued" and r.retry_count == 1
    r = _fresh(db_session, model, exhausted.id)
    assert r.status == "failed" and r.retry_count == 3 and r.completed_at is not None
    assert r.error_message == "gave up"
    # The live lease and the queued row are untouched.
    r = _fresh(db_session, model, fresh_row.id)
    assert r.status == "processing" and r.retry_count == 0 and _naive(r.last_heartbeat) == _naive(live)
    assert _fresh(db_session, model, queued.id).status == "queued"


@pytest.mark.parametrize("model,make", QUEUES)
def test_reaper_default_decision_uses_max_retries(db_session, test_project, model, make):
    tx = JobTransitions(model, name="t")
    stale = _t(900)
    a = make(db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=stale, retry_count=0)
    b = make(db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=stale, retry_count=1)
    result = tx.requeue_or_fail_stale(db_session, cutoff=_t(300), max_retries=1)
    db_session.commit()
    assert result.requeued == [a.id] and result.failed == [b.id]


@pytest.mark.parametrize("model,make", QUEUES)
def test_reaper_rechecks_staleness_under_the_lock(db_session, test_project, model, make, monkeypatch):
    """A row that was stale when listed but heartbeated before it was locked
    must be skipped — that is the window the old select-then-mutate reapers
    left open."""
    tx = JobTransitions(model, name="t")
    stale = _t(900)
    job = make(db_session, test_project.id, status="processing", started_at=stale, last_heartbeat=stale)

    real_execute = db_session.execute
    revived = {"done": False}

    def execute_with_race(stmt, *a, **k):
        # After the candidate listing (the first statement), a "live worker"
        # renews the heartbeat before the per-row locking select runs.
        res = real_execute(stmt, *a, **k)
        if not revived["done"]:
            revived["done"] = True
            real_execute(
                model.__table__.update().where(model.id == job.id).values(last_heartbeat=_t(1))
            )
        return res

    monkeypatch.setattr(db_session, "execute", execute_with_race)
    result = tx.requeue_or_fail_stale(db_session, cutoff=_t(300), max_retries=2)
    monkeypatch.undo()
    db_session.commit()
    assert result.total == 0
    row = _fresh(db_session, model, job.id)
    assert row.status == "processing" and row.retry_count == 0
