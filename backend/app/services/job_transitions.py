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


# A reap decision: given the locked row, its reap counter incremented, return
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

    def __init__(self, model: Type[Any], *, name: str, reap_counter: str = "retry_count"):
        self.model = model
        self.name = name
        # The column the reaper counts its own re-queues in.  The report
        # queue's ``retry_count`` counts nothing else; ingestion's also
        # counts failures and operator retries, so it has a separate one.
        self.reap_counter = reap_counter

    # ------------------------------------------------------------------
    # claim
    # ------------------------------------------------------------------
    def claim_oldest_queued(
        self, db: Session, *, message: Optional[str] = None, first: Sequence[Any] = (),
    ) -> Optional[Tuple[int, datetime]]:
        """Lock the oldest ``queued`` row (``FOR UPDATE SKIP LOCKED``) and flip
        it to ``processing``, stamping ``started_at`` / ``last_heartbeat``
        with the claim instant.  Returns ``(job_id, claimed_at)`` — the
        second element is this attempt's token — or ``None`` when the queue
        is empty.  The caller commits (still inside the lock) and then runs
        the job outside the transaction.

        ``first`` is what a queue ranks ahead of age (the report queue takes
        an issued report's render before any preview); the oldest still wins
        among equals.  It is the one claim: a queue with its own order passes
        it here and never repeats the lock and the flip."""
        m = self.model
        stmt = (
            select(m.id)
            .where(m.status == "queued")
            .order_by(*first, m.created_at)
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
    def _fenced(self, job_id: int, claimed_at: datetime):
        """This attempt's row: ``processing`` under the token its claim
        wrote.  There is no unfenced form — a claim always returns a token,
        and a missing one (None) matches no processing row."""
        m = self.model
        return [m.id == job_id, m.status == "processing", m.started_at == claimed_at]

    def heartbeat(
        self, db: Session, job_id: int, claimed_at: datetime, **cols: Any,
    ) -> int:
        """Renew ``last_heartbeat`` (and any extra columns, e.g. ``progress``)
        for THIS attempt only."""
        m = self.model
        now = datetime.now(timezone.utc)
        res = db.execute(
            update(m)
            .where(*self._fenced(job_id, claimed_at))
            .values(last_heartbeat=now, **cols)
        )
        return res.rowcount

    def stamp(
        self, db: Session, job_id: int, claimed_at: datetime, **cols: Any,
    ) -> int:
        """Write ``cols`` on the row for THIS attempt only, changing neither
        the status nor the heartbeat (review 2026-10-01 R1: the scan an
        attempt is writing).  Fenced like every other attempt write, so a
        stale attempt cannot put its scan on a row a peer now owns."""
        m = self.model
        res = db.execute(update(m).where(*self._fenced(job_id, claimed_at)).values(**cols))
        return res.rowcount

    def complete(
        self, db: Session, job_id: int, claimed_at: datetime, **cols: Any,
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
        claimed_at: datetime,
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

    def release(
        self, db: Session, job_id: int, claimed_at: datetime, **cols: Any,
    ) -> int:
        """processing → queued for THIS attempt only: the worker is stopping
        and hands the job back (the reaper's requeue, without waiting out its
        stale window).  ``retry_count`` is not touched — being interrupted by
        a restart is not a failed attempt."""
        m = self.model
        values: Dict[str, Any] = {
            "status": "queued", "started_at": None, "last_heartbeat": None, "completed_at": None,
        }
        values.update(cols)
        res = db.execute(update(m).where(*self._fenced(job_id, claimed_at)).values(**values))
        return res.rowcount

    # ------------------------------------------------------------------
    # locked read-check-write transitions (operator actions)
    # ------------------------------------------------------------------
    def _locked(self, db: Session, job_id: int, *extra_conds: Any):
        m = self.model
        # populate_existing (v2.368.0): a caller that already loaded this row in
        # the same session (an endpoint's visibility check, say) would otherwise
        # get its identity-mapped object back with the attributes it read
        # BEFORE the lock — and the "check under the lock" would be checking a
        # stale status. The lock is only worth what is read after it.
        stmt = (
            select(m).where(m.id == job_id, *extra_conds)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
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

    def acknowledge(
        self,
        db: Session,
        job_id: int,
        *,
        precondition: Callable[[Any], Optional[str]],
        column: str = "dismissed_at",
        extra_conds: Sequence[Any] = (),
        now: Optional[datetime] = None,
    ) -> Optional[Any]:
        """v2.403.0 — lock the row and stamp ``column`` (the operator's
        dismissal) if ``precondition(job)`` returns ``None``; otherwise raise
        :class:`JobNotTransitionable` with the reason it returned as the
        ``status``.  The status is not changed.  A row already stamped is
        returned untouched (idempotent).  Checked under the lock because what
        makes a job dismissable (its status, a later import of its file) can
        change while the request is in flight.  Returns the row, ``None`` if
        not found."""
        job = self._locked(db, job_id, *extra_conds)
        if job is None:
            return None
        if getattr(job, column) is not None:
            return job
        reason = precondition(job)
        if reason:
            raise JobNotTransitionable(job_id, reason, ("dismissable",))
        setattr(job, column, now or datetime.now(timezone.utc))
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
        attempt_is_over: Optional[Callable[[int, datetime], bool]] = None,
    ) -> ReapResult:
        """Reap stale ``processing`` rows.

        Candidates are listed first (cheap, uses the partial index on
        ``status = 'processing'``), then **each is re-selected under
        ``FOR UPDATE SKIP LOCKED`` with the stale predicate re-applied**.  A
        candidate that a live worker heartbeated (or completed) between the
        list and the lock no longer matches and is skipped — that is the
        window the old select-then-mutate reapers left open.  A row another
        reaper holds is skipped too.

        For each locked row the queue's reap counter (``reap_counter``) is
        incremented, then ``decide`` (default: requeue while it is
        ``<= max_retries``) chooses
        ``("requeue", cols)`` — status back to ``queued``, token and
        heartbeat cleared — or ``("fail", cols)`` — status ``failed``,
        ``completed_at`` stamped.  ``cols`` are applied on top so each
        queue words its own ``message`` / ``error_message``.

        ``attempt_is_over(job_id, started_at)`` is a queue's exact answer to
        "is the worker of this attempt gone?" (ingestion: nobody holds the
        attempt's liveness lock).  With it, a ``processing`` row whose lease
        is still inside the window is reaped as well when it answers True —
        re-selected under the row lock as the SAME attempt (``started_at``
        unchanged), so a row finished or re-claimed in between is skipped.
        The lease's age remains the rule for an attempt it cannot vouch for."""
        m = self.model
        # (job id, the predicate it must still satisfy under the row lock)
        candidates: List[Tuple[int, Tuple[Any, ...]]] = [
            (int(r[0]), self.stale_condition(cutoff))
            for r in db.execute(select(m.id).where(*self.stale_condition(cutoff)).order_by(m.id)).all()
        ]
        if attempt_is_over is not None:
            stale_ids = {job_id for job_id, _ in candidates}
            for job_id, started_at in db.execute(
                select(m.id, m.started_at).where(m.status == "processing").order_by(m.id)
            ).all():
                if job_id in stale_ids or started_at is None:
                    continue
                if attempt_is_over(int(job_id), started_at):
                    candidates.append(
                        (int(job_id), (m.status == "processing", m.started_at == started_at))
                    )
        result = ReapResult()
        if not candidates:
            return result
        now = datetime.now(timezone.utc)
        for job_id, still in candidates:
            stmt = (
                select(m)
                .where(m.id == job_id, *still)
                .with_for_update(skip_locked=True)
            )
            job = db.execute(stmt).scalar_one_or_none()
            if job is None:
                logger.debug(
                    "%s reaper: job %s no longer stale (heartbeated, finished, or "
                    "held by a peer) — skipped", self.name, job_id,
                )
                continue
            reaps = (getattr(job, self.reap_counter) or 0) + 1
            setattr(job, self.reap_counter, reaps)
            if decide is not None:
                action, cols = decide(job)
            else:
                action, cols = ("requeue" if reaps <= max_retries else "fail"), {}
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
