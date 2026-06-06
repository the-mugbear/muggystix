"""Regression tests for v2.87.0 recon-detail child-list pagination.

Pre-v2.87.0 ``GET /projects/{pid}/recon-sessions/{id}`` returned every
``IngestionJob`` and every ``TestPlan`` linked to the session in one
shot.  A long-running recon (hundreds of incremental uploads) or a
plan-heavy workflow (same recon drafted multiple plan versions)
shipped the entire child arrays on every detail load and every
10-second active-poll refresh.

v2.87.0 paginates both child lists.  These tests confirm:
  * limit caps reject oversize (server-side guard, not client trust);
  * skip + limit page correctly with the standard "skip past total →
    empty page" boundary;
  * ``uploads_total`` / ``plans_total`` always reflect the full row
    count regardless of the page being viewed;
  * ``all_scan_ids`` carries every IngestionJob's scan_id from the
    full session so the Inventory CTA stays correct as the operator
    pages through uploads.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models_agent import (
    ReconSession,
    ReconSessionStatus,
    TestPlan,
    TestPlanStatus,
)


def _seed_recon_with_uploads_and_plans(
    db_session,
    project,
    agent,
    *,
    upload_count: int,
    plan_count: int,
):
    """Create one scope + one recon session with N uploads and M plans."""
    scope = models.Scope(project_id=project.id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    session = ReconSession(
        project_id=project.id,
        scope_id=scope.id,
        agent_id=agent.id,
        status=ReconSessionStatus.ACTIVE.value,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.flush()
    # Each upload row also gets a Scan so all_scan_ids has something
    # to surface; one scan per ingestion job mirrors the real
    # ingestion-worker behaviour.
    for i in range(upload_count):
        scan = models.Scan(
            project_id=project.id,
            filename=f"upload-{i:03d}.xml",
            scan_type="nmap",
        )
        db_session.add(scan)
        db_session.flush()
        db_session.add(
            models.IngestionJob(
                project_id=project.id,
                recon_session_id=session.id,
                filename=f"upload-{i:03d}.xml",
                # NOT NULL fields — the ingestion-worker writes these
                # from the operator's upload.  Fixture supplies dummy
                # values just to satisfy the constraints.
                original_filename=f"upload-{i:03d}.xml",
                storage_path=f"/tmp/upload-{i:03d}.xml",
                status="completed",
                scan_id=scan.id,
            )
        )
    for i in range(plan_count):
        plan = TestPlan(
            project_id=project.id,
            agent_id=agent.id,
            source_recon_session_id=session.id,
            # uq_test_plan_project_version is (project_id, version) so
            # multiple plans in the same project need distinct versions.
            version=i + 1,
            title=f"plan-{i:03d}",
            description="",
            status=TestPlanStatus.APPROVED.value,
        )
        db_session.add(plan)
    db_session.flush()
    return session


def test_uploads_limit_cap(client, db_session, test_project, test_agent):
    """``uploads_limit`` rejects oversize requests (cap = 500)."""
    session = _seed_recon_with_uploads_and_plans(
        db_session, test_project, test_agent, upload_count=1, plan_count=0,
    )
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"uploads_limit": 999_999},
    )
    assert r.status_code == 422, r.text


def test_plans_limit_cap(client, db_session, test_project, test_agent):
    """``plans_limit`` rejects oversize requests (cap = 500)."""
    session = _seed_recon_with_uploads_and_plans(
        db_session, test_project, test_agent, upload_count=0, plan_count=1,
    )
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"plans_limit": 999_999},
    )
    assert r.status_code == 422, r.text


def test_uploads_pagination_and_total(client, db_session, test_project, test_agent):
    """skip + limit pages correctly; ``uploads_total`` always reflects
    the full row count regardless of the slice."""
    session = _seed_recon_with_uploads_and_plans(
        db_session, test_project, test_agent, upload_count=5, plan_count=0,
    )

    # Page 1: first 2 rows.
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"uploads_skip": 0, "uploads_limit": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uploads_total"] == 5
    assert body["uploads_skip"] == 0
    assert body["uploads_limit"] == 2
    assert len(body["uploads"]) == 2

    # Page 3: tail (1 row remaining, requested 2).
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"uploads_skip": 4, "uploads_limit": 2},
    )
    body = r.json()
    assert body["uploads_total"] == 5
    assert len(body["uploads"]) == 1

    # Past the end: empty slice but total still correct.
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"uploads_skip": 99, "uploads_limit": 50},
    )
    body = r.json()
    assert body["uploads_total"] == 5
    assert body["uploads"] == []


def test_plans_pagination_and_total(client, db_session, test_project, test_agent):
    """Same shape as uploads — pagination + total + boundary."""
    session = _seed_recon_with_uploads_and_plans(
        db_session, test_project, test_agent, upload_count=0, plan_count=4,
    )
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"plans_skip": 0, "plans_limit": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plans_total"] == 4
    assert body["plans_skip"] == 0
    assert body["plans_limit"] == 2
    assert len(body["plans_generated"]) == 2


def test_all_scan_ids_independent_of_uploads_slice(
    client, db_session, test_project, test_agent,
):
    """``all_scan_ids`` carries every IngestionJob's scan_id regardless
    of which uploads page is loaded — drives the Inventory CTA."""
    session = _seed_recon_with_uploads_and_plans(
        db_session, test_project, test_agent, upload_count=4, plan_count=0,
    )
    # Request only the first 1 upload row.
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
        params={"uploads_skip": 0, "uploads_limit": 1},
    )
    body = r.json()
    # uploads slice is 1 row...
    assert len(body["uploads"]) == 1
    # ...but all_scan_ids carries every scan from the session.
    assert len(body["all_scan_ids"]) == 4
    # And each id is a real integer (the fixture creates one Scan per
    # IngestionJob; the response shouldn't include nulls).
    assert all(isinstance(s, int) for s in body["all_scan_ids"])


def test_default_pagination_returns_first_page(client, db_session, test_project, test_agent):
    """When the caller omits skip/limit, the server defaults to skip=0
    + limit=50 — both child lists fit on page 1 for the default."""
    session = _seed_recon_with_uploads_and_plans(
        db_session, test_project, test_agent, upload_count=3, plan_count=2,
    )
    r = client.get(
        f"/api/v1/projects/{test_project.id}/recon-sessions/{session.id}",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uploads_skip"] == 0
    assert body["uploads_limit"] == 50
    assert body["plans_skip"] == 0
    assert body["plans_limit"] == 50
    assert len(body["uploads"]) == 3
    assert len(body["plans_generated"]) == 2
