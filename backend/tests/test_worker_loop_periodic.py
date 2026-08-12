"""Periodic maintenance must not starve under a continuously-full queue.

Regression for the external-review finding: the drain loop advanced its
periodic cadence only after ``poll_one()`` returned False (queue empty), so a
worker fed jobs at or above its own throughput never ran orphan reaping,
backlog warnings, or cleanup — precisely during the overload those exist to
surface. The fix runs periodic on a monotonic deadline regardless of queue
state; this pins that.
"""
import logging

from app import worker_loop


def _run_with_full_queue(monkeypatch, tmp_path, poll_interval, periodic_every_ticks):
    """Drive run_listen_loop with a queue that never empties (poll_one always
    True). A periodic callback flips the shutdown flag so the loop terminates
    once maintenance has demonstrably run despite the full queue."""
    monkeypatch.setattr(worker_loop, "_open_listen_connection", lambda channel: object())
    monkeypatch.setattr(worker_loop, "write_heartbeat", lambda *a, **k: None)
    # select must never be reached in the full-queue path, but stub it so a
    # stray call can't block on a real socket.
    monkeypatch.setattr(worker_loop.select, "select", lambda *a, **k: ([], [], []))

    state = {"polls": 0, "periodic": 0}

    def poll_one():
        state["polls"] += 1
        return True  # the queue is never empty

    def periodic():
        state["periodic"] += 1
        worker_loop._shutdown = True  # stop the loop once maintenance has run

    worker_loop._shutdown = False
    try:
        worker_loop.run_listen_loop(
            channel="test",
            poll_one=poll_one,
            heartbeat_path=str(tmp_path / "hb"),
            logger=logging.getLogger("test-worker"),
            poll_interval=poll_interval,
            periodic=[periodic],
            periodic_every_ticks=periodic_every_ticks,
        )
    finally:
        worker_loop._shutdown = False
    return state


def test_periodic_runs_even_when_queue_never_drains(monkeypatch, tmp_path):
    state = _run_with_full_queue(monkeypatch, tmp_path, poll_interval=0.05, periodic_every_ticks=1)
    # The whole point: periodic fired at least once while poll_one kept
    # returning True. (Pre-fix this would hang until the queue emptied, which
    # it never does, so the test would time out instead of asserting.)
    assert state["periodic"] >= 1
    # And it did so mid-drain, not after — many polls happened first.
    assert state["polls"] >= 1
