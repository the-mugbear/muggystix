"""Shared LISTEN/poll worker loop.

Both background workers — the ingestion worker (``app.worker``) and the report
worker (``app.report_worker``) — follow the same pattern: a Postgres ``LISTEN``
connection woken by ``pg_notify``, a poll fallback on timeout, cooperative
shutdown on SIGTERM/SIGINT, and a heartbeat file the container healthcheck reads
(a wedged loop stops refreshing it → the healthcheck flips unhealthy; v2.194.0).

This module is the single home for that loop so the two workers can't drift —
each just supplies its channel, its ``poll_one`` claim+process callable, its
heartbeat path, and any periodic maintenance callbacks (reaper / cleanup).
"""
from __future__ import annotations

import logging
import select
import signal
import time
from typing import Callable, List, Optional

from app.db.session import engine

# Cooperative-shutdown flag, flipped by the signal handlers and honored at every
# sleep/loop checkpoint so SIGTERM drains in <1s.
_shutdown = False

# Reconnect backoff — exponential, capped, so a Postgres blip doesn't stall a
# worker for minutes or hot-loop on a dead connection.
_RECONNECT_INITIAL_DELAY = 1.0
_RECONNECT_MAX_DELAY = 30.0


def install_signal_handlers(logger: logging.Logger) -> None:
    def _handle(signum: int, _frame: object) -> None:
        global _shutdown
        logger.info("Received signal %s — shutting down after current job", signum)
        _shutdown = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def is_shutting_down() -> bool:
    return _shutdown


def write_heartbeat(path: str, logger: logging.Logger) -> None:
    """Rewrite the liveness file (mtime is what the healthcheck checks).

    Called from the MAIN loop, not a daemon thread — a thread would keep ticking
    even if the loop wedged, defeating the point.  An unhealthy worker is only
    surfaced, not restarted (Docker restarts on process exit), so a transient
    stale window during a long single job is visible, not disruptive."""
    try:
        with open(path, "w") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        logger.warning("Could not write heartbeat to %s: %s", path, exc)


def _open_listen_connection(channel: str):
    """Open a fresh raw DB connection in LISTEN mode (autocommit) on ``channel``."""
    raw_conn = engine.raw_connection()
    raw_conn.set_isolation_level(0)  # autocommit, required for LISTEN
    cur = raw_conn.cursor()
    cur.execute(f"LISTEN {channel}")
    return raw_conn


def run_listen_loop(
    *,
    channel: str,
    poll_one: Callable[[], bool],
    heartbeat_path: str,
    logger: logging.Logger,
    poll_interval: float = 5.0,
    periodic: Optional[List[Callable[[], None]]] = None,
    periodic_every_ticks: int = 12,
) -> None:
    """Run the worker loop until shutdown.

    ``poll_one()`` claims and processes one queued job, returning ``True`` if it
    did work (so a backlog drains before we wait) and ``False`` when the queue is
    empty.  ``periodic`` callables (reaper, cleanup) run every
    ``periodic_every_ticks`` idle ticks.  The heartbeat is rewritten at the top of
    every cycle and after every job so a backlog drain keeps it fresh.

    Wrapped in an outer reconnect loop so a Postgres restart / blip doesn't kill
    the worker — DB errors close the connection, log, back off, and retry.
    """
    periodic = periodic or []
    backoff = _RECONNECT_INITIAL_DELAY
    # Write one heartbeat immediately so the file exists from the start (the
    # healthcheck's start_period covers the connect window).
    write_heartbeat(heartbeat_path, logger)

    while not _shutdown:
        raw_conn = None
        try:
            raw_conn = _open_listen_connection(channel)
            logger.info(
                "Worker connected — LISTEN %s, polling every %.0fs", channel, poll_interval,
            )
            backoff = _RECONNECT_INITIAL_DELAY  # reset on a clean connect
            tick = 0
            while not _shutdown:
                write_heartbeat(heartbeat_path, logger)
                # Drain any queued jobs first (there may be several).
                while not _shutdown:
                    try:
                        did_work = poll_one()
                    except Exception:
                        logger.exception("Unexpected error in poll_one")
                        did_work = False
                    write_heartbeat(heartbeat_path, logger)
                    if not did_work:
                        break

                tick = (tick + 1) % periodic_every_ticks
                if tick == 0:
                    for fn in periodic:
                        try:
                            fn()
                        except Exception:
                            logger.exception("Unexpected error in periodic task")

                if _shutdown:
                    break

                # Wait for a notification or timeout; a dead socket raises and
                # bubbles to the reconnect handler below.
                if select.select([raw_conn], [], [], poll_interval) == ([], [], []):
                    pass  # timeout — loop polls again
                else:
                    raw_conn.poll()
                    while raw_conn.notifies:
                        raw_conn.notifies.pop(0)
        except Exception as exc:
            logger.error(
                "Worker DB connection lost (%s: %s) — reconnecting in %.1fs",
                type(exc).__name__, exc, backoff,
            )
        finally:
            if raw_conn is not None:
                try:
                    raw_conn.close()
                except Exception:
                    pass

        if _shutdown:
            break

        slept = 0.0
        while slept < backoff and not _shutdown:
            time.sleep(min(1.0, backoff - slept))
            slept += 1.0
        backoff = min(backoff * 2.0, _RECONNECT_MAX_DELAY)
