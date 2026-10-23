"""Who may act on an upload job: a project ANALYST or above, whoever submitted
it (owner, 2026-10-23; review H-ING1).

Until then every job route asked "the submitter, or a GLOBAL admin": the
import history showed Dismiss / Retry / Keep / Cancel to every project analyst
and the click was a 403, and an agent's upload (``submitted_by_id`` NULL) could
be cleared by no one but a global admin, so it stayed in "imports needing
someone".  The rule is now the project role, the one the page's controls
follow (``useProjectRole``), and the live queue lists the project's jobs.
"""
from datetime import datetime, timezone

import pytest

from app.api.deps import get_current_user
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app


def _member(db, project, user_id: int, username: str, role: ProjectRole) -> User:
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


def _job(db, project, *, status, submitted_by_id, storage_path, partial=False):
    job = models.IngestionJob(
        project_id=project.id, filename="scan.xml", original_filename="scan.xml",
        storage_path=str(storage_path), status=status, partial=partial,
        error_message="boom" if status == "failed" else None,
        completed_at=datetime.now(timezone.utc) if status in ("failed", "completed") else None,
        submitted_by_id=submitted_by_id, retry_count=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@pytest.fixture
def people(db_session, test_project):
    """Two global MEMBERS of the project: the one who uploaded, and the
    analyst who acts on it."""
    uploader = _member(db_session, test_project, 801, "uploader", ProjectRole.ANALYST)
    actor = _member(db_session, test_project, 802, "colleague", ProjectRole.ANALYST)
    return uploader, actor


@pytest.fixture
def stored(tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    return f


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


SUBMITTERS = ["colleague", "agent"]


def _submitter(people, which):
    return people[0].id if which == "colleague" else None


@pytest.mark.parametrize("which", SUBMITTERS)
def test_a_project_analyst_dismisses_another_members_or_an_agents_upload(
    client, db_session, test_project, people, stored, which,
):
    job = _job(db_session, test_project, status="failed",
               submitted_by_id=_submitter(people, which), storage_path=stored)
    _as(people[1])
    r = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job.id}/dismiss")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(models.IngestionJob, job.id).dismissed_at is not None


@pytest.mark.parametrize("which", SUBMITTERS)
@pytest.mark.parametrize("keep", [False, True])
def test_a_project_analyst_retries_or_keeps_another_members_or_an_agents_upload(
    client, db_session, test_project, people, stored, which, keep,
):
    job = _job(db_session, test_project, status="failed",
               submitted_by_id=_submitter(people, which), storage_path=stored)
    _as(people[1])
    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/{job.id}/retry",
        json={"keep_truncated": keep},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


@pytest.mark.parametrize("which", SUBMITTERS)
def test_a_project_analyst_cancels_another_members_or_an_agents_upload(
    client, db_session, test_project, people, stored, which,
):
    job = _job(db_session, test_project, status="queued",
               submitted_by_id=_submitter(people, which), storage_path=stored)
    _as(people[1])
    r = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job.id}/cancel")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(models.IngestionJob, job.id).status == "failed"


@pytest.mark.parametrize("which", SUBMITTERS)
def test_a_project_analyst_reads_and_lists_the_projects_jobs(
    client, db_session, test_project, people, stored, which,
):
    job = _job(db_session, test_project, status="queued",
               submitted_by_id=_submitter(people, which), storage_path=stored)
    _as(people[1])
    base = f"/api/v1/projects/{test_project.id}/upload/jobs"
    assert client.get(f"{base}/{job.id}").status_code == 200
    assert job.id in [j["id"] for j in client.get(base).json()]
    assert [j["id"] for j in client.get(base, params={"ids": str(job.id)}).json()] == [job.id]


def test_bulk_discard_and_dismiss_take_the_projects_jobs(
    client, db_session, test_project, people, stored,
):
    staged = _job(db_session, test_project, status="staged", submitted_by_id=None, storage_path=stored)
    _as(people[1])
    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/discard-staged",
        json={"job_ids": [staged.id]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["job_ids"] == [staged.id]


@pytest.mark.parametrize("role", [ProjectRole.VIEWER, ProjectRole.AUDITOR])
def test_below_analyst_every_job_route_is_refused(
    client, db_session, test_project, people, stored, role,
):
    failed = _job(db_session, test_project, status="failed", submitted_by_id=None, storage_path=stored)
    queued = _job(db_session, test_project, status="queued", submitted_by_id=None, storage_path=stored)
    reader = _member(db_session, test_project, 810 + len(role.value), f"r-{role.value}", role)
    _as(reader)
    base = f"/api/v1/projects/{test_project.id}/upload/jobs"
    answers = [
        client.post(f"{base}/{failed.id}/dismiss"),
        client.post(f"{base}/{failed.id}/retry", json={"keep_truncated": True}),
        client.post(f"{base}/{queued.id}/cancel"),
        client.get(f"{base}/{failed.id}"),
        client.get(f"{base}/{failed.id}/detection"),
        client.get(base),
    ]
    assert [a.status_code for a in answers] == [403] * len(answers)
    db_session.expire_all()
    assert db_session.get(models.IngestionJob, failed.id).dismissed_at is None
    assert db_session.get(models.IngestionJob, failed.id).status == "failed"
    assert db_session.get(models.IngestionJob, queued.id).status == "queued"


def test_another_projects_job_is_not_found(client, db_session, test_project, people, stored):
    from app.db.models_project import Project
    other = Project(name="other", slug="other-proj", description="x")
    db_session.add(other)
    db_session.commit()
    job = _job(db_session, other, status="failed", submitted_by_id=None, storage_path=stored)
    _as(people[1])
    r = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job.id}/dismiss")
    assert r.status_code == 404
