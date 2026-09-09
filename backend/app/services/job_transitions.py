"""Shared job-lifecycle transitions for the ingestion and report queues.

Both queues (``ingestion_jobs``, ``report_jobs``) run the same machine:

    queued --claim--> processing --complete--> completed
                          |  \\--fail-------> failed --retry--> queued
                          \\--reap (stale)--> queued | failed
    queued --cancel--> failed (ingestion) | cancelled (report)

Until v2.328.0 each service carried its own copy of the SQL, and they had
drifted: report completion was fenced on the claim token while ingestion's
was not; cancellation read-checked-wrote without a lock; both reapers
selected stale candidates and mutated them without re-checking the stale
predicate under a lock, so a worker that woke up between the SELECT and the
UPDATE could have its live attempt re-queued underneath it.  This module is
the one place those rules live.

Rules every write here enforces:

* **The claim token.**  ``claim_oldest_queued`` stamps ``started_at`` with
  the claim instant and returns it.  ``heartbeat``, ``complete`` and ``fail``
  condition on ``status = 'processing' AND started_at = :claimed`` so an
  attempt whose lease was reaped and re-claimed by a peer can neither keep
  the new owner's heartbeat warm, publish over its result, nor fail it.
  Every one of them returns the affected rowcount; **0 means "not yours any
  more"** and the caller decides how loudly to say so.
* **Locked read-check-write.**  ``cancel``, ``retry`` and the reaper lock
  the row (``FOR UPDATE``, ``SKIP LOCKED`` for the reaper) and evaluate the
  precondition inside the lock, so a concurrent claim cannot interleave.
* **No commits.**  Every function runs its statements on the caller's
  session and leaves the transaction open — the service owns the commit
  (and the log line, and any follow-on notification).

Dialect: everything is SQLAlchemy Core against the model's table, so the
``DateTime`` type binds the token the same way on Postgres (timestamptz)
and SQLite (naive text) and ``with_for_update`` is rendered only where the
dialect supports it.  ``skip_locked`` is what lets N workers poll the same
queue without serialising on each other.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Type

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class JobNotTransitionable(ValueError):
    """The row exists but is not in a state the requested transition accepts.

    ``status`` carries the state it was actually in so the caller can word
    its own error (the report API turns it into a 409)."""

    def __init__(self, job_id: int, status: str, wanted: Sequence[str]):
        self.job_id = job_id
        self.status = status
        self.wanted = tuple(wanted)
        super().__init__(
            f"job {job_id} is '{status}', expected one of {', '.join(self.wanted)}"
        )


@dataclass
class ReapResult:
    """What the reaper did.  ``ids`` is every row it touched, in order."""

    requeued: List[int] = field(default_factory=list)
    failed: List[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.requeued) + len(self.failed)


# A reap decision: given the locked, retry_count-incremented row, return
# ("requeue", extra_values) or ("fail", extra_values).  The module sets the
# status / token / completed_at columns itself; the callback supplies the
# queue-specific message text and side conditions (e.g. "is the upload still
# on disk?").
ReapDecision = Callable[[Any], Tuple[str, Dict[str, Any]]]


class JobTransitions:
    """Lifecycle transitions for one queue table.

    ``model`` is the ORM class (``IngestionJob`` / ``ReportJob``).  Both
    carry the columns this needs: ``id``, ``status``, ``started_at``,
    ``last_heartbeat``, ``completed_at``, ``retry_count``, ``created_at``.
    """

    def __init__(self, model: Type[Any], *, name: str):
        self.model = model
        self.name = name

    # ------------------------------------------------------------------
    # claim
    # ------------------------------------------------------------------
    def claim_oldest_queued(
        self, db: Session, *, message: Optional[str] = None,
    ) -> Optional[Tuple[int, datetime]]:
        """Lock the oldest ``queued`` row (``FOR UPDATE SKIP LOCKED``) and flip
        it to ``processing``, stamping ``started_at`` / ``last_heartbeat``
        with the claim instant.  Returns ``(job_id, claimed_at)`` — the
        second element is this attempt's token — or ``None`` when the queue
        is empty.  The caller commits (still inside the lock) and then runs
        the job outside the transaction."""
        m = self.model
        stmt = (
            select(m.id)
            .where(m.status == "queued")
            .order_by(m.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = db.execute(stmt).first()
        if row is None:
            return None
        job_id = int(row[0])
        claimed_at = datetime.now(timezone.utc)
        values: Dict[str, Any] = {
            "status": "processing",
            "started_at": claimed_at,
            "last_heartbeat": claimed_at,
        }
        if message is not None:
            values["message"] = message
        db.execute(update(m).where(m.id == job_id).values(**values))
        return job_id, claimed_at

    # ------------------------------------------------------------------
    # fenced writes — rowcount 0 == "this attempt no longer owns the row"
    # ------------------------------------------------------------------
    def _fenced(self, job_id: int, claimed_at: Optional[datetime]):
        m = self.model
        conds = [m.id == job_id, m.status == "processing"]
        if claimed_at is not None:
            conds.append(m.started_at == claimed_at)
        return conds

    def heartbeat(
        self, db: Session, job_id: int, claimed_at: Optional[datetime], **cols: Any,
    ) -> int:
        """Renew ``last_heartbeat`` (and any extra columns, e.g. ``progress``).

        Fenced on the token when one is given.  ``claimed_at=None`` is the
        legacy unfenced form — it still requires ``status = 'processing'``
        so a heartbeat can never resurrect a terminal row, but it cannot
        tell two attempts apart; new call sites must pass the token."""
        m = self.model
        now = datetime.now(timezone.utc)
        res = db.execute(
            update(m)
            .where(*self._fenced(job_id, claimed_at))
            .values(last_heartbeat=now, **cols)
        )
        return res.rowcount

    def complete(
        self, db: Session, job_id: int, claimed_at: Optional[datetime], **cols: Any,
    ) -> int:
        """processing → completed for THIS attempt only.  ``completed_at`` is
        stamped unless the caller supplies it."""
        m = self.model
        values = {"status": "completed", "completed_at": datetime.now(timezone.utc)}
        values.update(cols)
        res = db.execute(update(m).where(*self._fenced(job_id, claimed_at)).values(**values))
        return res.rowcount

    def fail(
        self,
        db: Session,
        job_id: int,
        claimed_at: Optional[datetime],
        *,
        increment_retry: bool = False,
        **cols: Any,
    ) -> int:
        """processing → failed for THIS attempt only.  ``increment_retry``
        bumps ``retry_count`` atomically (ingestion counts each attempt; the
        report queue counts only reaper retries)."""
        m = self.model
        values: Dict[str, Any] = {"status": "failed", "completed_at": datetime.now(timezone.utc)}
        if increment_retry:
            values["retry_count"] = func.coalesce(m.retry_count, 0) + 1
        values.update(cols)
        res = db.execute(update(m).where(*self._fenced(job_id, claimed_at)).values(**values))
        return res.rowcount

    # ------------------------------------------------------------------
    # locked read-check-write transitions (operator actions)
    # ------------------------------------------------------------------
    def _locked(self, db: Session, job_id: int, *extra_conds: Any):
        m = self.model
        stmt = select(m).where(m.id == job_id, *extra_conds).with_for_update()
        return db.execute(stmt).scalar_one_or_none()

    def cancel(
        self,
        db: Session,
        job_id: int,
        *,
        allowed_from: Sequence[str] = ("queued",),
        to_status: str = "cancelled",
        extra_conds: Sequence[Any] = (),
        **cols: Any,
    ) -> Optional[Any]:
        """Lock the row, require ``status in allowed_from``, move it to
        ``to_status`` with ``completed_at`` stamped plus ``cols``.

        Returns the (still-locked, mutated) ORM row; ``None`` if no row
        matched ``job_id`` + ``extra_conds`` (the report queue scopes by
        project); raises :class:`JobNotTransitionable` otherwise.  Holding
        the lock across the check and the write is the whole point: a
        worker's ``SKIP LOCKED`` claim cannot land in between."""
        job = self._locked(db, job_id, *extra_conds)
        if job is None:
            return None
        if job.status not in allowed_from:
            raise JobNotTransitionable(job_id, job.status, allowed_from)
        job.status = to_status
        job.completed_at = datetime.now(timezone.utc)
        for k, v in cols.items():
            setattr(job, k, v)
        return job

    def retry(
        self,
        db: Session,
        job_id: int,
        *,
        allowed_from: Sequence[str] = ("failed",),
        extra_conds: Sequence[Any] = (),
        precondition: Optional[Callable[[Any], Optional[str]]] = None,
        increment_retry: bool = False,
        **cols: Any,
    ) -> Optional[Any]:
        """Lock the row, require ``status in allowed_from`` (and
        ``precondition(job)`` returning ``None``), and put it back to
        ``queued`` with the claim token and terminal columns cleared.

        ``precondition`` may return a reason string; it is raised as
        :class:`JobNotTransitionable` with that reason as the ``status`` so
        the caller can map it to its own code (ingestion: "file_missing").
        Returns the mutated row, ``None`` if not found."""
        job = self._locked(db, job_id, *extra_conds)
        if job is None:
            return None
        if job.status not in allowed_from:
            raise JobNotTransitionable(job_id, job.status, allowed_from)
        if precondition is not None:
            reason = precondition(job)
            if reason:
                raise JobNotTransitionable(job_id, reason, allowed_from)
        if increment_retry:
            job.retry_count = (job.retry_count or 0) + 1
        job.status = "queued"
        job.started_at = None
        job.last_heartbeat = None
        job.completed_at = None
        job.error_message = None
        job.last_error = None
        for k, v in cols.items():
            setattr(job, k, v)
        return job

    # ------------------------------------------------------------------
    # reaper
    # ------------------------------------------------------------------
    def stale_condition(self, cutoff: datetime):
        """``processing`` rows whose lease is older than ``cutoff``: no
        heartbeat ever and started before the cutoff, or last heartbeat
        before the cutoff."""
        m = self.model
        return (
            m.status == "processing",
            or_(
                (m.last_heartbeat.is_(None)) & (m.started_at < cutoff),
                m.last_heartbeat < cutoff,
            ),
        )

    def requeue_or_fail_stale(
        self,
        db: Session,
        *,
        cutoff: datetime,
        max_retries: int,
        decide: Optional[ReapDecision] = None,
    ) -> ReapResult:
        """Reap stale ``processing`` rows.

        Candidates are listed first (cheap, uses the partial index on
        ``status = 'processing'``), then **each is re-selected under
        ``FOR UPDATE SKIP LOCKED`` with the stale predicate re-applied**.  A
        candidate that a live worker heartbeated (or completed) between the
        list and the lock no longer matches and is skipped — that is the
        window the old select-then-mutate reapers left open.  A row another
        reaper holds is skipped too.

        For each locked row ``retry_count`` is incremented, then ``decide``
        (default: requeue while ``retry_count <= max_retries``) chooses
        ``("requeue", cols)`` — status back to ``queued``, token and
        heartbeat cleared — or ``("fail", cols)`` — status ``failed``,
        ``completed_at`` stamped.  ``cols`` are applied on top so each
        queue words its own ``message`` / ``error_message``."""
        m = self.model
        ids = [
            int(r[0])
            for r in db.execute(select(m.id).where(*self.stale_condition(cutoff)).order_by(m.id)).all()
        ]
        result = ReapResult()
        if not ids:
            return result
        now = datetime.now(timezone.utc)
        for job_id in ids:
            stmt = (
                select(m)
                .where(m.id == job_id, *self.stale_condition(cutoff))
                .with_for_update(skip_locked=True)
            )
            job = db.execute(stmt).scalar_one_or_none()
            if job is None:
                logger.debug(
                    "%s reaper: job %s no longer stale (heartbeated, finished, or "
                    "held by a peer) — skipped", self.name, job_id,
                )
                continue
            job.retry_count = (job.retry_count or 0) + 1
            if decide is not None:
                action, cols = decide(job)
            else:
                action, cols = ("requeue" if job.retry_count <= max_retries else "fail"), {}
            if action == "requeue":
                job.status = "queued"
                job.started_at = None
                job.last_heartbeat = None
                job.completed_at = None
                result.requeued.append(job_id)
            elif action == "fail":
                job.status = "failed"
                job.completed_at = now
                result.failed.append(job_id)
            else:  # pragma: no cover — programmer error
                raise ValueError(f"reap decision must be 'requeue' or 'fail', got {action!r}")
            for k, v in cols.items():
                setattr(job, k, v)
        return result
