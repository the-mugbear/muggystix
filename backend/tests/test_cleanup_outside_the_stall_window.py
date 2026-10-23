"""The partial-scan cleanup is not held to the stall window (review
2026-10-23, R-ING1; 2026-10-23b, ING-3).

``_discard_partial_scan`` runs at the claim (a dead attempt's scan) and in
process (a failed attempt's) — one long step with no heartbeat.  It ran under
the import's ``statement_timeout`` (the stall window) and without feeding the
stall watchdog, so the cleanup of a very large scan could be cancelled part
way (the scan stays; at the claim the job then fails) or read as a stall.
Feeding the watchdog before and after was not enough: after a stall the mark
was already set, and the worker exited ``STALL_GRACE_S`` later, mid-DELETE.
Now the watchdog gives no verdict while the cleanup runs, its transaction
lifts the statement timeout, and each lock wait stays bounded.
"""
from __future__ import annotations

from sqlalchemy import text

from app.services import ingestion_service as ingestion_module
from app.services.ingestion_service import IngestionService
from app.services.ingestion_stall import StallWatchdog, stall_statement_timeout


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _watchdog(clock, acted):
    return StallWatchdog(60, grace_s=30, clock=clock, fail_and_exit=lambda: acted.append(clock.now))


_REMOVED = {"hosts": 0, "ports": 0, "observations": 0, "rehomed": 0, "dns_records": 0, "dns_names": 0}


def test_the_cleanup_is_never_read_as_a_stall_and_lifts_the_statement_timeout(db_session, monkeypatch):
    from app.core.config import settings

    seen, acted, clock = {}, [], _Clock()
    watchdog = _watchdog(clock, acted)
    monkeypatch.setattr(settings, "INGESTION_STALL_TIMEOUT", 1800)

    def fake_delete(db, scan_id, *, lock_timeout_s=None):
        seen["timeout"] = db.execute(text("SHOW statement_timeout")).scalar()
        seen["lock_timeout"] = db.execute(text("SHOW lock_timeout")).scalar()
        # A cleanup far longer than the window plus its grace: no verdict.
        for _ in range(10):
            clock.now += 50
            assert watchdog.check() is False
        seen["stalled"] = watchdog.stalled
        return _REMOVED

    monkeypatch.setattr(ingestion_module, "delete_partial_scan", fake_delete)
    monkeypatch.setattr(ingestion_module._active_job, "watchdog", watchdog, raising=False)

    with stall_statement_timeout(db_session, 1800):
        # The import's own transactions are held to the window …
        db_session.rollback()
        assert db_session.execute(text("SHOW statement_timeout")).scalar() == "30min"
        assert IngestionService()._discard_partial_scan(db_session, 424242, why="test") is True
        # … and the next one is again (SET LOCAL ended with the cleanup's).
        db_session.rollback()
        assert db_session.execute(text("SHOW statement_timeout")).scalar() == "30min"

    # Its lock waits are bounded by the window; its working statements are not.
    assert seen == {"timeout": "0", "lock_timeout": "30min", "stalled": False}
    assert acted == []
    # The window starts again when the cleanup ends.
    clock.now += 59
    assert watchdog.check() is False and not watchdog.stalled


def test_a_cleanup_after_a_stall_is_not_cut_short_by_the_grace(db_session, monkeypatch):
    """The stall that led here had already set the mark, so the cleanup had
    what was left of the grace before the worker exited mid-DELETE."""
    acted, clock = [], _Clock()
    watchdog = _watchdog(clock, acted)
    clock.now += 61
    watchdog.check()
    assert watchdog.stalled

    def slow_delete(db, scan_id, *, lock_timeout_s=None):
        for _ in range(10):
            clock.now += 50
            assert watchdog.check() is False
        return _REMOVED

    monkeypatch.setattr(ingestion_module, "delete_partial_scan", slow_delete)
    monkeypatch.setattr(ingestion_module._active_job, "watchdog", watchdog, raising=False)
    assert IngestionService()._discard_partial_scan(db_session, 424242, why="test") is True
    assert acted == []
    # The mark stays — the parse it marked is stopped all the same — and what
    # follows the cleanup (failing the job) has the whole grace again.
    assert watchdog.stalled
    clock.now += 29
    assert watchdog.check() is False and acted == []
    clock.now += 2
    assert watchdog.check() is True and acted == [clock.now]


def test_a_lock_timeout_the_caller_gives_is_its_own(db_session, monkeypatch):
    seen = {}

    def fake_delete(db, scan_id, *, lock_timeout_s=None):
        seen["lock_timeout"] = db.execute(text("SHOW lock_timeout")).scalar()
        seen["given"] = lock_timeout_s
        return _REMOVED

    monkeypatch.setattr(ingestion_module, "delete_partial_scan", fake_delete)
    monkeypatch.setattr(ingestion_module._active_job, "watchdog", None, raising=False)
    assert IngestionService()._discard_partial_scan(db_session, 424242, why="test", lock_timeout_s=5) is True
    # ``lock_project_for_cleanup`` sets it around the project lock.
    assert seen == {"lock_timeout": "0", "given": 5}


def test_a_failed_cleanup_starts_the_window_again_too(db_session, monkeypatch):
    acted, clock = [], _Clock()
    watchdog = _watchdog(clock, acted)

    def failing_delete(db, scan_id, *, lock_timeout_s=None):
        clock.now += 500
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(ingestion_module, "delete_partial_scan", failing_delete)
    monkeypatch.setattr(ingestion_module._active_job, "watchdog", watchdog, raising=False)
    assert IngestionService()._discard_partial_scan(db_session, 424242, why="test") is False
    assert watchdog.check() is False and not watchdog.stalled and acted == []
