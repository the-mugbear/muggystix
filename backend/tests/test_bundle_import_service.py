"""Contract tests for bundle_import_service — the remote-agent
results-file ingestion path.

High-value regressions to catch:
  - idempotent re-import (upserts, doesn't duplicate)
  - partial vs final semantics
  - cross-plan / cross-bundle rejection
  - is_final rejection when results empty or parse errors collected
  - JSON depth bomb guard (audit H1)
  - strict is_finding bool check (audit #8)
"""

from __future__ import annotations

import json
import pytest

from app.services.bundle_import_service import (
    BundleImportError,
    _assert_json_depth_ok,
    _load_file,
    import_results_file,
)


# ---------------------------------------------------------------------------
# Pure-function tests (no DB)
# ---------------------------------------------------------------------------

class TestJsonDepthGuard:
    def test_reasonable_depth_ok(self):
        _assert_json_depth_ok('{"a": {"b": {"c": [1, 2, 3]}}}')

    def test_20_levels_at_limit_ok(self):
        """20 nested brackets is the exact limit — should pass."""
        text = "[" * 20 + "1" + "]" * 20
        _assert_json_depth_ok(text)

    def test_21_levels_rejected(self):
        text = "[" * 21 + "1" + "]" * 21
        with pytest.raises(BundleImportError, match="nested too deeply"):
            _assert_json_depth_ok(text)

    def test_50_levels_rejected(self):
        text = '{"a":' * 50 + '1' + '}' * 50
        with pytest.raises(BundleImportError, match="nested too deeply"):
            _assert_json_depth_ok(text)

    def test_braces_in_strings_dont_count(self):
        """A string literal containing { should not inflate depth."""
        text = '{"msg": "open brace { everywhere {{{{"}'
        _assert_json_depth_ok(text)  # should not raise

    def test_escaped_quote_in_string(self):
        text = '{"msg": "escaped quote \\" and brace {"}'
        _assert_json_depth_ok(text)


class TestLoadFile:
    def test_valid_json_object(self):
        data = _load_file(b'{"bundle_id": "abc", "results": []}')
        assert data["bundle_id"] == "abc"

    def test_invalid_utf8_rejected(self):
        with pytest.raises(BundleImportError, match="UTF-8"):
            _load_file(b'\xff\xfe\x00\x00')

    def test_malformed_json_rejected(self):
        with pytest.raises(BundleImportError, match="JSON"):
            _load_file(b'{"bundle_id": "abc",,}')

    def test_non_object_root_rejected(self):
        with pytest.raises(BundleImportError, match="object at the top level"):
            _load_file(b'[1, 2, 3]')

    def test_depth_bomb_rejected(self):
        bomb = ("[" * 30) + "1" + ("]" * 30)
        with pytest.raises(BundleImportError, match="nested too deeply"):
            _load_file(bomb.encode())


# ---------------------------------------------------------------------------
# End-to-end import tests (real DB fixtures)
# ---------------------------------------------------------------------------

@pytest.fixture
def exported_session(db_session, test_project, test_plan, test_agent):
    """Create an ExecutionSession in exported mode with a bundle_id
    and a single TestPlanEntry, matching what bundle_service.build_export_bundle
    would have produced.
    """
    from app.db import models
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionMode, ExecutionSessionStatus,
        TestPlanEntry,
    )

    # Create a host belonging to the project so the entry has a valid
    # host_id — host_id is a non-null FK on TestPlanEntry.
    host = models.Host(
        ip_address="10.0.0.5",
        hostname="target.example.com",
        state="up",
        project_id=test_project.id,
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    entry = TestPlanEntry(
        test_plan_id=test_plan.id,
        host_id=host.id,
        priority="high",
        test_phase="enumeration",
        proposed_tests=[
            {"tool": "nmap", "description": "scan", "command": "nmap {ip}"},
            {"tool": "curl", "description": "fetch", "command": "curl {ip}"},
        ],
        rationale="contract test entry",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    session = ExecutionSession(
        test_plan_id=test_plan.id,
        agent_id=test_agent.id,
        started_by_id=None,
        status=ExecutionSessionStatus.ACTIVE.value,
        mode=ExecutionSessionMode.EXPORTED.value,
        bundle_id="bundle-fixture-abc123",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    return {"session": session, "entry": entry, "host": host, "plan": test_plan}


def _build_results_payload(fixture, *, is_final=False, results=None, extras=None):
    payload = {
        "bundle_id": fixture["session"].bundle_id,
        "plan_id": fixture["plan"].id,
        "execution_session_id": fixture["session"].id,
        "is_final": is_final,
        "results": results if results is not None else [],
        "sanity_checks": [],
    }
    if extras:
        payload.update(extras)
    return json.dumps(payload).encode()


class TestImportValidation:
    def test_missing_bundle_id_rejected(self, db_session, test_project):
        with pytest.raises(BundleImportError, match="bundle_id"):
            import_results_file(
                db_session,
                plan_id=1,
                project_id=test_project.id,
                file_bytes=b'{"results": []}',
                filename="bad.json",
                imported_by_id=None,
            )

    def test_plan_id_mismatch_rejected(self, db_session, test_project, exported_session):
        """A file claiming plan_id=999 against endpoint plan_id=real
        must be rejected — audit C3 cross-validation."""
        payload = json.dumps({
            "bundle_id": exported_session["session"].bundle_id,
            "plan_id": 99999,  # intentionally wrong
            "results": [],
        }).encode()
        with pytest.raises(BundleImportError, match="plan_id"):
            import_results_file(
                db_session,
                plan_id=exported_session["plan"].id,
                project_id=test_project.id,
                file_bytes=payload,
                filename="mismatch.json",
                imported_by_id=None,
            )

    def test_session_id_mismatch_rejected(self, db_session, test_project, exported_session):
        payload = json.dumps({
            "bundle_id": exported_session["session"].bundle_id,
            "plan_id": exported_session["plan"].id,
            "execution_session_id": 99999,  # wrong
            "results": [],
        }).encode()
        with pytest.raises(BundleImportError, match="execution_session_id"):
            import_results_file(
                db_session,
                plan_id=exported_session["plan"].id,
                project_id=test_project.id,
                file_bytes=payload,
                filename="mismatch.json",
                imported_by_id=None,
            )

    def test_unknown_bundle_id_rejected(self, db_session, test_project, test_plan):
        payload = json.dumps({
            "bundle_id": "nonexistent",
            "results": [],
        }).encode()
        with pytest.raises(BundleImportError, match="No exported execution session"):
            import_results_file(
                db_session,
                plan_id=test_plan.id,
                project_id=test_project.id,
                file_bytes=payload,
                filename="x.json",
                imported_by_id=None,
            )


class TestImportFinalSemantics:
    def test_is_final_with_empty_results_rejected(
        self, db_session, test_project, exported_session
    ):
        """Audit C3: can't finalize without results."""
        payload = _build_results_payload(exported_session, is_final=True, results=[])
        with pytest.raises(BundleImportError, match="results array is empty"):
            import_results_file(
                db_session,
                plan_id=exported_session["plan"].id,
                project_id=test_project.id,
                file_bytes=payload,
                filename="x.json",
                imported_by_id=None,
            )

    def test_is_final_must_be_strict_bool(
        self, db_session, test_project, exported_session
    ):
        """'is_final': 'true' (string) must NOT be treated as True."""
        entry_id = exported_session["entry"].id
        payload = json.dumps({
            "bundle_id": exported_session["session"].bundle_id,
            "plan_id": exported_session["plan"].id,
            "execution_session_id": exported_session["session"].id,
            "is_final": "true",   # string, not bool
            "results": [
                {"entry_id": entry_id, "test_index": 0, "status": "executed"}
            ],
        }).encode()
        summary = import_results_file(
            db_session,
            plan_id=exported_session["plan"].id,
            project_id=test_project.id,
            file_bytes=payload,
            filename="x.json",
            imported_by_id=None,
        )
        # String "true" doesn't flip the flag — session stays active
        assert summary["is_final"] is False
        assert summary["session_status"] == "active"

    def test_partial_import_leaves_session_active(
        self, db_session, test_project, exported_session
    ):
        entry_id = exported_session["entry"].id
        payload = _build_results_payload(
            exported_session,
            is_final=False,
            results=[
                {"entry_id": entry_id, "test_index": 0, "status": "executed",
                 "command_run": "nmap 10.0.0.5", "raw_output": "open"},
            ],
        )
        summary = import_results_file(
            db_session,
            plan_id=exported_session["plan"].id,
            project_id=test_project.id,
            file_bytes=payload,
            filename="x.json",
            imported_by_id=None,
        )
        assert summary["results_imported"] == 1
        assert summary["session_status"] == "active"


class TestImportIdempotency:
    def test_reimport_upserts_not_duplicates(
        self, db_session, test_project, exported_session
    ):
        """Re-uploading the same file should update the existing row,
        not create a second TestExecutionResult for the same
        (session, entry, test_index)."""
        from app.db.models_agent import TestExecutionResult
        entry_id = exported_session["entry"].id
        session_id = exported_session["session"].id

        payload = _build_results_payload(
            exported_session,
            is_final=False,
            results=[
                {"entry_id": entry_id, "test_index": 0, "status": "executed",
                 "command_run": "first-run", "raw_output": "v1",
                 "findings_summary": "initial"},
            ],
        )

        # First import
        import_results_file(
            db_session,
            plan_id=exported_session["plan"].id,
            project_id=test_project.id,
            file_bytes=payload,
            filename="x.json",
            imported_by_id=None,
        )
        db_session.commit()
        first_count = db_session.query(TestExecutionResult).filter(
            TestExecutionResult.execution_session_id == session_id
        ).count()
        assert first_count == 1

        # Re-import with updated content
        payload2 = _build_results_payload(
            exported_session,
            is_final=False,
            results=[
                {"entry_id": entry_id, "test_index": 0, "status": "executed",
                 "command_run": "second-run", "raw_output": "v2",
                 "findings_summary": "updated"},
            ],
        )
        import_results_file(
            db_session,
            plan_id=exported_session["plan"].id,
            project_id=test_project.id,
            file_bytes=payload2,
            filename="x.json",
            imported_by_id=None,
        )
        db_session.commit()
        second_count = db_session.query(TestExecutionResult).filter(
            TestExecutionResult.execution_session_id == session_id
        ).count()
        # Still 1 row — the upsert replaced, didn't append
        assert second_count == 1

        row = db_session.query(TestExecutionResult).filter(
            TestExecutionResult.execution_session_id == session_id
        ).first()
        assert row.command_run == "second-run"
        assert row.raw_output == "v2"


class TestIsFindingBoolStrict:
    """Audit #8 — 'false' string must not become True via bool()."""

    def test_is_finding_false_string_warned_not_truthy(
        self, db_session, test_project, exported_session
    ):
        entry_id = exported_session["entry"].id
        payload = _build_results_payload(
            exported_session,
            results=[
                {"entry_id": entry_id, "test_index": 0, "status": "executed",
                 "is_finding": "false"},  # string, not bool
            ],
        )
        summary = import_results_file(
            db_session,
            plan_id=exported_session["plan"].id,
            project_id=test_project.id,
            file_bytes=payload,
            filename="x.json",
            imported_by_id=None,
        )
        # Session has autoflush=False — flush explicitly so the pending
        # insert is visible to the subsequent query.
        db_session.flush()
        # The row was imported (status was valid) but is_finding must
        # end up False because "false" != True identity
        from app.db.models_agent import TestExecutionResult
        row = db_session.query(TestExecutionResult).filter(
            TestExecutionResult.entry_id == entry_id
        ).first()
        assert row is not None
        assert row.is_finding is False
        # And a parse error was collected so the user knows
        assert any("is_finding" in e for e in summary["parse_errors"])

    def test_is_finding_true_bool_accepted(
        self, db_session, test_project, exported_session
    ):
        entry_id = exported_session["entry"].id
        payload = _build_results_payload(
            exported_session,
            results=[
                {"entry_id": entry_id, "test_index": 0, "status": "executed",
                 "is_finding": True, "severity": "high"},
            ],
        )
        import_results_file(
            db_session,
            plan_id=exported_session["plan"].id,
            project_id=test_project.id,
            file_bytes=payload,
            filename="x.json",
            imported_by_id=None,
        )
        db_session.flush()
        from app.db.models_agent import TestExecutionResult
        row = db_session.query(TestExecutionResult).filter(
            TestExecutionResult.entry_id == entry_id
        ).first()
        assert row is not None
        assert row.is_finding is True
