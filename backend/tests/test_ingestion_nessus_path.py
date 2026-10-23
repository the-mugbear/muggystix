"""
Regression test for the Nessus branch of `IngestionService._execute_parser`.

The v2.55.0 tool_name_hint mismatch feature initialised `warnings_parts`
inside the generic-parser `else:` branch but referenced it
unconditionally at the return.  Any Nessus upload therefore hit
``UnboundLocalError`` before the job could be marked completed (the
exception then propagated up to `_run_job`'s broad except, which marked
the job 'failed' with the wrong reason).  v2.55.1 hoists the
initialisation alongside `parse_stats` and moves the mismatch check
AFTER the if/else convergence, so:

  * the Nessus branch no longer crashes
  * tool_name_hint mismatch warnings now apply to Nessus uploads too

These tests mock ``NessusIntegrationService.process_nessus_file`` so we
don't depend on a real .nessus fixture or the full integration stack.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.ingestion_service import ingestion_service
from app.services.nessus_integration_service import NessusIntegrationService


@pytest.fixture
def fake_nessus_job():
    """A bare IngestionJob-ish object with the attributes `_execute_parser`
    reads.  Avoids needing a real DB row for these unit-level checks."""
    job = MagicMock()
    job.id = 9001
    job.options = {}
    job.storage_path = "/tmp/fake.nessus"
    job.original_filename = "fake.nessus"
    job.project_id = None
    return job


def test_nessus_path_no_unbound_local_error(db_session, fake_nessus_job):
    """The pre-v2.55.1 regression: a successful Nessus parse must not
    crash with UnboundLocalError before the return."""
    fake_result = {
        "success": True,
        "scan_id": 12345,
        "message": "Nessus scan processed",
    }

    with patch.object(
        NessusIntegrationService,
        "process_nessus_file",
        return_value=fake_result,
    ):
        result = ingestion_service._execute_parser(
            db=db_session,
            job=fake_nessus_job,
            parser_class=NessusIntegrationService,
            description="Nessus vulnerability scan",
        )

    assert result["scan_id"] == 12345
    assert result["tool_name"] == "Nessus"
    assert result["skipped_count"] == 0
    # No hint was set → no mismatch warning → parser_warnings is None
    assert result["parser_warnings"] is None


def test_nessus_path_surfaces_hint_mismatch(db_session, fake_nessus_job):
    """A Nessus parse with a non-matching tool_name_hint must produce a
    parser_warnings message.  Pre-v2.55.1, the mismatch check was
    confined to the generic-parser branch and never ran for Nessus."""
    fake_nessus_job.options = {"tool_name_hint": "openvas"}
    fake_result = {
        "success": True,
        "scan_id": 12345,
        "message": "Nessus scan processed",
    }

    with patch.object(
        NessusIntegrationService,
        "process_nessus_file",
        return_value=fake_result,
    ):
        result = ingestion_service._execute_parser(
            db=db_session,
            job=fake_nessus_job,
            parser_class=NessusIntegrationService,
            description="Nessus vulnerability scan",
        )

    assert result["parser_warnings"], "expected a mismatch warning for openvas→Nessus"
    assert "openvas" in result["parser_warnings"].lower()
    assert "nessus" in result["parser_warnings"].lower()


def test_nessus_path_no_warning_when_hint_matches(db_session, fake_nessus_job):
    """Hint == 'nessus' (any case) should not produce a warning."""
    fake_nessus_job.options = {"tool_name_hint": "Nessus"}
    fake_result = {
        "success": True,
        "scan_id": 12345,
        "message": "Nessus scan processed",
    }

    with patch.object(
        NessusIntegrationService,
        "process_nessus_file",
        return_value=fake_result,
    ):
        result = ingestion_service._execute_parser(
            db=db_session,
            job=fake_nessus_job,
            parser_class=NessusIntegrationService,
            description="Nessus vulnerability scan",
        )

    assert result["parser_warnings"] is None


def test_nessus_losses_make_the_job_partial_and_skipped(db_session, fake_nessus_job):
    """review 2026-10-23b, ING-2: the service's failure counts were read only
    as warning text — the job completed partial=False, skipped_count=0 and
    never reached Needs attention, Blocked or the agents' ingestion issues."""
    fake_result = {
        "partial": True,
        "scan_id": 12345,
        "message": "Nessus scan processed",
        "host_processing_failures": 2,
        "vuln_write_failures": 3,
        "report_items_unreadable": 1,
        "warnings": ["2 host(s) failed to process and were skipped."],
    }
    with patch.object(NessusIntegrationService, "process_nessus_file", return_value=fake_result):
        result = ingestion_service._execute_parser(
            db=db_session, job=fake_nessus_job,
            parser_class=NessusIntegrationService, description="Nessus vulnerability scan",
        )
    assert result["partial"] is True
    assert result["skipped_count"] == 6
    assert "2 host(s) failed" in result["parser_warnings"]


def test_a_nessus_import_that_lost_an_item_ends_partial_on_the_job_row(
    db_session, test_project, tmp_path, monkeypatch,
):
    from app.parsers.nessus_parser import NessusParser
    from tests.ingestion_job_harness import run_file
    from tests.test_nessus_skip_informational import NESSUS_XML

    real = NessusParser._parse_vulnerability

    def unreadable(self, report_item):
        if report_item.get("pluginID") == "90317":
            raise ValueError("malformed report item")
        return real(self, report_item)

    monkeypatch.setattr(NessusParser, "_parse_vulnerability", unreadable)
    path = tmp_path / "lost.nessus"
    path.write_text(NESSUS_XML)
    job = run_file(db_session, test_project.id, path)
    assert job.status == "completed", (job.status, job.error_message)
    assert job.partial is True
    assert job.skipped_count == 1
