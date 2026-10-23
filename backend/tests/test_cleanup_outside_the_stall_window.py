"""The partial-scan cleanup is not held to the stall window (review
2026-10-23, R-ING1).

``_discard_partial_scan`` runs at the claim (a dead attempt's scan) and in
process (a failed attempt's) — one long step with no heartbeat.  It ran under
the import's ``statement_timeout`` (the stall window) and without feeding the
stall watchdog, so the cleanup of a very large scan could be cancelled part
way (the scan stays; at the claim the job then fails) or read as a stall.
Now the watchdog's window starts again before and after it, and its
transaction lifts the statement timeout.
"""
from __future__ import annotations

from sqlalchemy import text

from app.services import ingestion_service as ingestion_module
from app.services.ingestion_service import IngestionService
from app.services.ingestion_stall import stall_statement_timeout


class _Watchdog:
    stalled = False

    def __init__(self):
        self.fed = 0

    def progressed(self):
        self.fed += 1


def test_the_cleanup_feeds_the_watchdog_and_lifts_the_statement_timeout(db_session, monkeypatch):
    seen = {}
    watchdog = _Watchdog()

    def fake_delete(db, scan_id, *, lock_timeout_s=None):
        seen["timeout"] = db.execute(text("SHOW statement_timeout")).scalar()
        seen["fed_before"] = watchdog.fed
        return {"hosts": 0, "ports": 0, "observations": 0, "rehomed": 0, "dns_records": 0, "dns_names": 0}

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

    assert seen["timeout"] == "0"
    assert seen["fed_before"] == 1  # the window starts again when the cleanup begins
    assert watchdog.fed == 2  # … and when it ends


def test_a_failed_cleanup_feeds_the_watchdog_too(db_session, monkeypatch):
    watchdog = _Watchdog()

    def failing_delete(db, scan_id, *, lock_timeout_s=None):
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(ingestion_module, "delete_partial_scan", failing_delete)
    monkeypatch.setattr(ingestion_module._active_job, "watchdog", watchdog, raising=False)
    assert IngestionService()._discard_partial_scan(db_session, 424242, why="test") is False
    assert watchdog.fed == 2
