"""An import is stopped when it stops making progress, never for taking long
(owner, 2026-10-17 — comprehensive review 2026-10-16, Q1 / T7).

Progress is the heartbeat: every parser reports between records
(``ProgressBeat``, ``report_progress``), so a heartbeat means records are
moving.  ``INGESTION_JOB_TIMEOUT`` was a wall-clock cap checked inside that
heartbeat — it stopped exactly the imports that WERE progressing (a large
Nessus file at minute 31) and never one that had stopped, because a wedged
parse does not heartbeat.

Three parts, one window (``settings.INGESTION_STALL_TIMEOUT``):

* ``stall_statement_timeout`` — every transaction of the import sets
  ``statement_timeout`` to the window, so Postgres itself cancels a statement
  (or a lock wait) that outlasts it: no heartbeat can happen during one
  statement, so one that long IS a stall.
* ``StallWatchdog`` — a thread beside the parse.  No heartbeat for the window:
  it marks the attempt stalled, and the next heartbeat — or the dispatcher,
  seeing the cancelled statement — fails the import with
  ``stall_message`` (the in-process cleanup removes its partial scan).  Still
  nothing ``STALL_GRACE_S`` later (a parse stuck in Python, which nothing
  inside the process can interrupt): it fails the job itself, fenced on the
  claim, and ends the worker process; Docker restarts it and the reaper,
  finding the attempt's liveness lock free, removes the partial scan.
* The reaper's time rule for a dead lease is a multiple of the same window.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from sqlalchemy import event, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: How long the watchdog waits, after marking a stall, for the parse to stop
#: by itself before it ends the worker process.
STALL_GRACE_S = 120.0
#: How often the watchdog looks.
POLL_S = 5.0
#: The exit status a worker ends with on a stall it could not interrupt.
STALL_EXIT_CODE = 70


def stall_message(window_s: int) -> str:
    minutes = max(1, round(window_s / 60))
    return (
        f"The import stopped making progress (nothing for {minutes} minute{'s' if minutes != 1 else ''}) "
        f"and was stopped; nothing from this file was kept. INGESTION_STALL_TIMEOUT sets that window."
    )


class StallWatchdog:
    """Watches one import attempt.  ``progressed()`` on every heartbeat;
    ``stalled`` once the window passed with none; ``fail_and_exit`` (fail the
    job, end the process) once the grace passed too.  The clock and the
    actions are arguments so a test can drive it."""

    def __init__(self, window_s: float, *, fail_and_exit: Callable[[], None],
                 grace_s: float = STALL_GRACE_S, poll_s: float = POLL_S,
                 clock: Callable[[], float] = time.monotonic):
        self.window_s = window_s
        self.grace_s = grace_s
        self.poll_s = poll_s
        self._clock = clock
        self._fail_and_exit = fail_and_exit
        self._lock = threading.Lock()
        self._last = clock()
        self._stalled_at: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def stalled(self) -> bool:
        with self._lock:
            return self._stalled_at is not None

    def progressed(self) -> None:
        with self._lock:
            self._last = self._clock()

    def check(self) -> bool:
        """One look; True when the watchdog is done (it acted, or was stopped)."""
        if self._stop.is_set():
            return True
        now = self._clock()
        with self._lock:
            idle = now - self._last
            if self._stalled_at is None:
                if idle > self.window_s:
                    self._stalled_at = now
                    logger.warning("Import attempt: no progress for %.0f s — marked stalled", idle)
                return False
            if now - self._stalled_at <= self.grace_s:
                return False
        logger.error("Import attempt: still no progress %.0f s after it was marked stalled — "
                     "failing the job and ending the worker", self.grace_s)
        self._fail_and_exit()
        return True

    def start(self) -> "StallWatchdog":
        def run() -> None:
            while not self._stop.wait(self.poll_s):
                try:
                    if self.check():
                        return
                except Exception:  # noqa: BLE001 — the watchdog must not die quietly mid-import
                    logger.exception("Stall watchdog check failed")
        self._thread = threading.Thread(target=run, name="ingestion-stall-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()


def exit_worker() -> None:
    """End the worker process at once.  ``os._exit``: the parse thread is
    stuck and would hold a normal exit; nothing it holds is lost — the job is
    already failed and its liveness lock goes with the connection."""
    os._exit(STALL_EXIT_CODE)


@contextmanager
def stall_statement_timeout(db: Session, window_s: int) -> Iterator[None]:
    """While inside, every transaction ``db`` begins sets ``statement_timeout``
    to the stall window (``SET LOCAL``: it ends with the transaction).
    Savepoints do not ask again; nothing happens off PostgreSQL."""
    bind = db.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql" or window_s <= 0:
        yield
        return
    stmt = text(f"SET LOCAL statement_timeout = '{int(window_s)}s'")

    def _set(session, transaction, connection):
        if not transaction.nested:
            connection.execute(stmt)

    event.listen(db, "after_begin", _set)
    try:
        if db.in_transaction():
            db.execute(stmt)
        yield
    finally:
        event.remove(db, "after_begin", _set)
