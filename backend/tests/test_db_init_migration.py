"""B2-1 Part 1 — boot-migration error classification.

A genuine migration defect (bad revision, constraint violation on real data)
must surface as ONE actionable CRITICAL line and re-raise, so the operator
sees it instead of a buried Alembic traceback when the container crash-loops.
A transient DB-not-ready (OperationalError) must re-raise UNTOUCHED so the
caller's retry loop handles it — it is not a migration defect.
"""
import logging

import pytest
from sqlalchemy.exc import OperationalError

import app.db.init as dbinit


class _FakeInspector:
    def get_table_names(self):
        # Force the "already Alembic-managed → upgrade head" branch.
        return ["alembic_version", "users"]


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def caprecords(monkeypatch):
    """Capture records straight off the module logger — the app sets its own
    handlers / propagation, so a handler attached here is the reliable hook."""
    monkeypatch.setattr(dbinit, "inspect", lambda engine: _FakeInspector())
    monkeypatch.setattr(dbinit, "_current_db_revision", lambda: "abc123")
    # Alembic's fileConfig(disable_existing_loggers=True) — run earlier in the
    # session by initialize_database() — leaves this logger .disabled; re-enable
    # it (the same fix _restore_app_logging applies in the worker).
    monkeypatch.setattr(dbinit.logger, "disabled", False)
    monkeypatch.setattr(dbinit.logger, "level", logging.DEBUG)
    handler = _Capture()
    dbinit.logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        dbinit.logger.removeHandler(handler)


def _patch_upgrade(monkeypatch, fn):
    import alembic.command as command
    monkeypatch.setattr(command, "upgrade", fn)


def test_migration_defect_logs_critical_and_reraises(monkeypatch, caprecords):
    def _boom(cfg, rev):
        raise ValueError("relation already exists")

    _patch_upgrade(monkeypatch, _boom)

    with pytest.raises(ValueError):
        dbinit._sync_schema_with_alembic()

    crits = [r for r in caprecords if r.levelno == logging.CRITICAL]
    assert any("MIGRATION FAILED" in r.getMessage() for r in crits)
    # The current revision is surfaced so the operator knows where it stopped.
    assert any("abc123" in r.getMessage() for r in crits)


def test_db_not_ready_reraises_without_critical(monkeypatch, caprecords):
    def _not_ready(cfg, rev):
        raise OperationalError("connection refused", None, Exception("boom"))

    _patch_upgrade(monkeypatch, _not_ready)

    with pytest.raises(OperationalError):
        dbinit._sync_schema_with_alembic()

    # A DB-readiness error is the retry loop's job — it must NOT be mislabelled
    # as a migration defect.
    assert not any(
        "MIGRATION FAILED" in r.getMessage()
        for r in caprecords
        if r.levelno == logging.CRITICAL
    )
