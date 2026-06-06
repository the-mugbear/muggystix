"""Contract tests for bundle_service.build_export_bundle.

The import side has far more logic (validation, idempotency,
cross-checks) and lives in test_bundle_import_service.  This module
covers the export happy path and the handful of error conditions
that must stay rejected so an operator can't export a plan that
isn't ready.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.services.bundle_service import build_export_bundle


@pytest.fixture
def plan_with_entries(db_session, test_project, test_plan, test_agent):
    """Attach a host + a TestPlanEntry to test_plan so it's exportable."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry

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
        priority="critical",
        test_phase="exploitation",
        proposed_tests=[
            {"tool": "nmap", "description": "scan", "command": "nmap -sV {ip}"},
        ],
        rationale="contract test",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return {"plan": test_plan, "entry": entry, "host": host}


class TestExportHappyPath:
    def test_zip_contains_four_files(self, db_session, plan_with_entries, test_user):
        bundle = build_export_bundle(
            db=db_session,
            request=None,
            plan=plan_with_entries["plan"],
            started_by_id=test_user.id,
            agent_id=None,
        )
        assert "zip_bytes" in bundle
        assert bundle["bundle_id"]
        assert bundle["execution_session_id"] is not None

        # Unpack the zip and check its contents
        zf = zipfile.ZipFile(io.BytesIO(bundle["zip_bytes"]))
        names = set(zf.namelist())
        assert names == {
            "manifest.json",
            "plan.json",
            "instructions.md",
            "results_schema.json",
        }

    def test_manifest_shape(self, db_session, plan_with_entries, test_user):
        bundle = build_export_bundle(
            db=db_session,
            request=None,
            plan=plan_with_entries["plan"],
            started_by_id=test_user.id,
            agent_id=None,
        )
        zf = zipfile.ZipFile(io.BytesIO(bundle["zip_bytes"]))
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["bundle_id"] == bundle["bundle_id"]
        assert manifest["plan_id"] == plan_with_entries["plan"].id
        assert manifest["execution_session_id"] == bundle["execution_session_id"]
        assert manifest["entry_count"] == 1
        assert "schema_version" in manifest
        assert "prompt_version" in manifest

    def test_plan_snapshot_contains_host_context(
        self, db_session, plan_with_entries, test_user
    ):
        bundle = build_export_bundle(
            db=db_session,
            request=None,
            plan=plan_with_entries["plan"],
            started_by_id=test_user.id,
            agent_id=None,
        )
        zf = zipfile.ZipFile(io.BytesIO(bundle["zip_bytes"]))
        plan_snapshot = json.loads(zf.read("plan.json"))
        assert plan_snapshot["plan_id"] == plan_with_entries["plan"].id
        assert len(plan_snapshot["entries"]) == 1
        entry = plan_snapshot["entries"][0]
        assert entry["host_ip"] == "10.0.0.5"
        assert entry["host_hostname"] == "target.example.com"
        assert entry["priority"] == "critical"

    def test_instructions_do_not_contain_api_key(
        self, db_session, plan_with_entries, test_user
    ):
        """Offline bundles don't mint an API key — the agent runs on the
        user's machine and results come back via import.  Make sure
        nothing leaked into the instructions file."""
        bundle = build_export_bundle(
            db=db_session,
            request=None,
            plan=plan_with_entries["plan"],
            started_by_id=test_user.id,
            agent_id=None,
        )
        zf = zipfile.ZipFile(io.BytesIO(bundle["zip_bytes"]))
        instructions = zf.read("instructions.md").decode()
        assert "nm_agent_" not in instructions
        assert "X-API-Key" not in instructions

    def test_creates_exported_session_row(
        self, db_session, plan_with_entries, test_user
    ):
        """Export should persist an ExecutionSession in exported mode
        so imports can later correlate via bundle_id."""
        from app.db.models_agent import ExecutionSession, ExecutionSessionMode
        bundle = build_export_bundle(
            db=db_session,
            request=None,
            plan=plan_with_entries["plan"],
            started_by_id=test_user.id,
            agent_id=None,
        )
        db_session.flush()
        session = db_session.query(ExecutionSession).filter(
            ExecutionSession.id == bundle["execution_session_id"]
        ).first()
        assert session is not None
        assert session.mode == ExecutionSessionMode.EXPORTED.value
        assert session.bundle_id == bundle["bundle_id"]

    def test_export_transitions_approved_to_in_progress(
        self, db_session, plan_with_entries, test_user
    ):
        plan = plan_with_entries["plan"]
        assert plan.status == "approved"
        build_export_bundle(
            db=db_session,
            request=None,
            plan=plan,
            started_by_id=test_user.id,
            agent_id=None,
        )
        db_session.flush()
        db_session.refresh(plan)
        assert plan.status == "in_progress"


class TestExportRejection:
    def test_empty_plan_rejected(self, db_session, test_plan, test_user):
        """A plan with no entries cannot be exported."""
        with pytest.raises(ValueError, match="empty"):
            build_export_bundle(
                db=db_session,
                request=None,
                plan=test_plan,
                started_by_id=test_user.id,
                agent_id=None,
            )

    def test_draft_plan_rejected(
        self, db_session, plan_with_entries, test_user
    ):
        """Only approved / in_progress plans can be exported."""
        plan = plan_with_entries["plan"]
        plan.status = "draft"
        db_session.commit()
        with pytest.raises(ValueError, match="status"):
            build_export_bundle(
                db=db_session,
                request=None,
                plan=plan,
                started_by_id=test_user.id,
                agent_id=None,
            )

    def test_rejected_plan_rejected(
        self, db_session, plan_with_entries, test_user
    ):
        plan = plan_with_entries["plan"]
        plan.status = "rejected"
        db_session.commit()
        with pytest.raises(ValueError, match="status"):
            build_export_bundle(
                db=db_session,
                request=None,
                plan=plan,
                started_by_id=test_user.id,
                agent_id=None,
            )
