"""Comprehensive review 2026-10-16, T7 (owner, 2026-10-17): an import is
stopped when it stops making progress, never for taking long.

The wall-clock cap (INGESTION_JOB_TIMEOUT) stopped imports that WERE
progressing and never one that had stopped (a wedged parse does not
heartbeat).  Pinned here: the watchdog's rule on a clock the test drives; a
statement that outlasts the window ends the import as a stall — no other
parser tried, nothing kept; every transaction of an import carries the
window as its statement timeout; the stall message names the setting.
"""
from __future__ import annotations

from sqlalchemy import text

from app.db import models
from app.services.ingestion_stall import StallWatchdog, stall_message
from tests.ingestion_job_harness import run_file


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_watchdog_marks_a_stall_then_acts_once_after_the_grace():
    clock, acted = _Clock(), []
    dog = StallWatchdog(60, grace_s=30, clock=clock, fail_and_exit=lambda: acted.append(clock.now))

    # Progress keeps it quiet, however long the import runs.
    for _ in range(10):
        clock.now += 50
        dog.progressed()
        assert dog.check() is False and not dog.stalled
    clock.now += 61
    assert dog.check() is False and dog.stalled and acted == []      # marked, not acted on
    clock.now += 29
    assert dog.check() is False and acted == []                      # within the grace
    clock.now += 2
    assert dog.check() is True and acted == [clock.now]              # the last resort, once


def test_a_stopped_watchdog_never_acts():
    clock, acted = _Clock(), []
    dog = StallWatchdog(1, grace_s=0, clock=clock, fail_and_exit=lambda: acted.append(1))
    dog.stop()
    clock.now += 100
    assert dog.check() is True and acted == []


def test_the_stall_message_names_the_setting():
    assert "INGESTION_STALL_TIMEOUT" in stall_message(1800)
    assert "30 minutes" in stall_message(1800) and "1 minute" in stall_message(30)


def _rustscan_file(tmp_path):
    path = tmp_path / "r.txt"
    path.write_text("Open 10.91.0.1:22\nOpen 10.91.0.2:22\n")
    return path


def test_every_transaction_of_an_import_carries_the_stall_window(db_session, test_project, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.parsers.rustscan_parser import RustScanParser

    if db_session.get_bind().dialect.name != "postgresql":
        import pytest
        pytest.skip("statement_timeout is PostgreSQL's")
    monkeypatch.setattr(settings, "INGESTION_STALL_TIMEOUT", 1234)
    seen = []
    real = RustScanParser.parse_file

    def watching(self, *a, **kw):
        seen.append(self.db.execute(text("SHOW statement_timeout")).scalar())
        return real(self, *a, **kw)

    monkeypatch.setattr(RustScanParser, "parse_file", watching)
    job = run_file(db_session, test_project.id, _rustscan_file(tmp_path))
    assert job.status == "completed", (job.status, job.error_message)
    assert seen == ["1234s"]


def test_a_statement_that_outlasts_the_window_stops_the_import_and_keeps_nothing(
    db_session, test_project, tmp_path, monkeypatch,
):
    """Postgres cancels the statement (here: a 10 ms timeout over a 1 s
    sleep); the dispatcher reads that as a stall — terminal, no fallback
    parser, the partial scan removed, the message naming the setting."""
    import pytest

    from app.parsers.parser_utils import ensure_scan
    from app.parsers.rustscan_parser import RustScanParser
    from app.services import ingestion_service

    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("statement_timeout is PostgreSQL's")
    tried = []

    def wedged(self, file_path, filename, **kw):
        ensure_scan(self.db, filename=filename, tool_name="rustscan", scan_type="port_scan",
                    project_id=kw.get("project_id"))
        self.db.execute(text("SET LOCAL statement_timeout = '10ms'"))
        self.db.execute(text("SELECT pg_sleep(1)"))

    monkeypatch.setattr(RustScanParser, "parse_file", wedged)
    real_execute = ingestion_service.IngestionService._execute_parser

    def recording(self, db, job, parser_class, *a, **kw):
        tried.append(parser_class.__name__)
        return real_execute(self, db, job, parser_class, *a, **kw)

    monkeypatch.setattr(ingestion_service.IngestionService, "_execute_parser", recording)
    job = run_file(db_session, test_project.id, _rustscan_file(tmp_path))

    assert job.status == "failed"
    assert "stopped making progress" in job.error_message and "INGESTION_STALL_TIMEOUT" in job.error_message
    assert tried == ["RustScanParser"]                       # no other parser over a stalled file
    assert db_session.query(models.Scan).filter(models.Scan.project_id == test_project.id).count() == 0
