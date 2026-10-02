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


# --- review 2026-10-01 B1 — what the failure line tells the operator ----------
# The upgrade is one transaction, so a revision that raises (or refuses, like
# b2e5a8c1d4f6 on duplicate scanner findings) leaves the database where it
# started: no restore.  Only a run that passed a mid-chain commit moved it.

def test_a_rolled_back_upgrade_says_nothing_changed_and_asks_for_no_restore():
    msg = dbinit._failure_message(
        "a1d4f7b9c2e3", "a1d4f7b9c2e3",
        RuntimeError("2 issue(s) have more than one scanner finding … Nothing was changed."),
    )
    assert msg.startswith("DATABASE MIGRATION FAILED — NOTHING WAS CHANGED")
    assert "still at revision a1d4f7b9c2e3" in msg
    assert "No database restore is needed" in msg
    assert "restoring the pre-deploy DB backup" not in msg
    assert "more than one scanner finding" in msg  # the revision's own instructions


def test_an_upgrade_that_stopped_partway_names_both_revisions_and_the_backup():
    msg = dbinit._failure_message("c3f9a1b7e240", "f1d2c3b4a5e6", ValueError("boom"))
    assert msg.startswith("DATABASE MIGRATION FAILED — schema left PARTWAY at revision f1d2c3b4a5e6")
    assert "started at c3f9a1b7e240" in msg
    assert "restoring the pre-deploy DB backup" in msg
    assert "NOTHING WAS CHANGED" not in msg


def test_a_failure_on_an_empty_database_is_described_without_a_revision():
    msg = dbinit._failure_message(None, None, ValueError("boom"))
    assert "NOTHING WAS CHANGED" in msg and "no schema version" in msg


# --- v2.370.1 — the test harness must not migrate the developer's database ----
# ``app.main`` runs initialize_database() at import. The suite mounts the
# working tree, so an unmerged revision was applied to a real dev database.

def _count_syncs(monkeypatch):
    calls = []
    monkeypatch.setattr(dbinit, "_sync_schema_with_alembic", lambda: calls.append(1))
    monkeypatch.setattr(dbinit, "_done", False)
    return calls


def test_initialize_database_is_skipped_when_the_harness_says_so(monkeypatch):
    calls = _count_syncs(monkeypatch)
    monkeypatch.setenv("BLUESTICK_SKIP_DB_INIT", "1")

    dbinit.initialize_database()

    assert calls == []
    # Not recorded as done: a later call without the flag must still migrate.
    assert dbinit._done is False


def test_initialize_database_still_migrates_by_default(monkeypatch):
    calls = _count_syncs(monkeypatch)
    monkeypatch.delenv("BLUESTICK_SKIP_DB_INIT", raising=False)

    dbinit.initialize_database()

    assert calls == [1]


def test_the_suite_itself_runs_with_the_flag_set():
    """conftest sets it before importing the app — if that line is lost, every
    test run migrates whatever DATABASE_URL points at again."""
    import os
    assert os.environ.get("BLUESTICK_SKIP_DB_INIT") == "1"
