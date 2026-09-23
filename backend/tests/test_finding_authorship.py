"""v2.375.0 — authored content on a finding belongs to its author.

A finding's title and the finding itself (delete) may be changed by the
finding's author or a project admin; a comment's text only by the comment's
author.  Triage (severity, owner, status) stays open to any analyst.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import Annotation
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.main import app


def _member(db_session, project, user_id: int, username: str, role: ProjectRole) -> User:
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="x", role=UserRole.MEMBER,
        is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db_session.commit()
    return user


@pytest.fixture
def act_as():
    """Switch the authenticated user for the rest of the test."""
    def _act(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user
    return _act


@pytest.fixture
def people(db_session, test_project):
    return {
        "alice": _member(db_session, test_project, 201, "alice", ProjectRole.ANALYST),
        "bob": _member(db_session, test_project, 202, "bob", ProjectRole.ANALYST),
        "padmin": _member(db_session, test_project, 203, "padmin", ProjectRole.ADMIN),
    }


def _create(client, project, title="SMB signing disabled"):
    r = client.post(f"/api/v1/projects/{project.id}/findings", json={"title": title, "severity": "high"})
    assert r.status_code == 201, r.text
    return r.json()


def test_the_author_may_rename_and_others_may_not(client, test_project, people, act_as):
    act_as(people["alice"])
    f = _create(client, test_project)
    assert f["created_by_id"] == people["alice"].id
    assert f["created_by_name"] == "Alice"
    assert f["can_modify"] is True
    url = f"/api/v1/projects/{test_project.id}/findings/{f['id']}"

    r = client.patch(url, json={"title": "  SMB signing not required  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "SMB signing not required"

    act_as(people["bob"])
    assert client.get(url).json()["can_modify"] is False
    r = client.patch(url, json={"title": "Bob's title"})
    assert r.status_code == 403, r.text
    assert client.get(url).json()["title"] == "SMB signing not required"
    # Triage stays open to any analyst — including resending the title unchanged.
    r = client.patch(url, json={"severity": "low", "title": "SMB signing not required"})
    assert r.status_code == 200, r.text
    assert r.json()["severity"] == "low"

    act_as(people["padmin"])
    assert client.get(url).json()["can_modify"] is True
    assert client.patch(url, json={"title": "Admin fixed it"}).status_code == 200


def test_an_empty_title_is_rejected(client, test_project):
    f = _create(client, test_project)
    r = client.patch(f"/api/v1/projects/{test_project.id}/findings/{f['id']}", json={"title": "   "})
    assert r.status_code == 422, r.text


def test_delete_is_author_or_admin_and_leaves_the_evidence(client, db_session, test_project, people, act_as):
    host = models.Host(project_id=test_project.id, ip_address="10.20.0.5", state="up")
    db_session.add(host)
    db_session.flush()
    source_note = Annotation(host_id=host.id, user_id=people["alice"].id, body="Anon SMB\nworks", note_type="finding")
    db_session.add(source_note)
    db_session.commit()

    act_as(people["alice"])
    promoted = client.post(
        f"/api/v1/projects/{test_project.id}/annotations/{source_note.id}/promote", json={"severity": "high"},
    )
    assert promoted.status_code == 201, promoted.text
    fid = promoted.json()["id"]
    base = f"/api/v1/projects/{test_project.id}/findings/{fid}"
    root = client.post(f"{base}/notes", json={"body": "repro"}).json()
    act_as(people["bob"])
    client.post(f"{base}/notes", json={"body": "seen too", "parent_id": root["id"]})

    # Bob did not record it.
    r = client.delete(base)
    assert r.status_code == 403, r.text

    act_as(people["alice"])
    r = client.delete(base)
    assert r.status_code == 204, r.text
    db_session.expire_all()
    assert db_session.get(Finding, fid) is None
    assert db_session.query(FindingHost).filter_by(finding_id=fid).count() == 0
    assert db_session.query(FindingStatusHistory).filter_by(finding_id=fid).count() == 0
    assert db_session.query(Annotation).filter_by(finding_id=fid).count() == 0
    # The source note survives and can be promoted again.
    assert db_session.get(Annotation, source_note.id) is not None
    again = client.post(
        f"/api/v1/projects/{test_project.id}/annotations/{source_note.id}/promote", json={"severity": "high"},
    )
    assert again.status_code == 201 and again.json()["id"] != fid
    # The deletion is on the audit log, since the finding's own history went with it.
    audit = db_session.query(AuditLog).filter_by(action="finding_deleted", resource_id=str(fid)).one()
    assert audit.user_id == people["alice"].id
    assert audit.details["title"] == "Anon SMB"
    assert audit.details["comment_count"] == 2


def test_a_project_admin_may_delete_anyones_finding(client, test_project, people, act_as):
    act_as(people["alice"])
    f = _create(client, test_project)
    act_as(people["padmin"])
    assert client.delete(f"/api/v1/projects/{test_project.id}/findings/{f['id']}").status_code == 204


def test_deleting_a_scanner_finding_returns_its_rows_to_untriaged(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="nessus.xml", tool_name="nessus")
    host = models.Host(project_id=test_project.id, ip_address="10.20.0.9", state="up")
    db_session.add_all([scan, host])
    db_session.flush()
    vuln = Vulnerability(
        host_id=host.id, scan_id=scan.id, plugin_id="57608", title="SMB Signing not required",
        severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NESSUS,
    )
    db_session.add(vuln)
    db_session.commit()
    f = client.post(
        f"/api/v1/projects/{test_project.id}/vulnerabilities/{vuln.id}/promote", json={"vuln_id": vuln.id},
    ).json()
    assert client.delete(f"/api/v1/projects/{test_project.id}/findings/{f['id']}").status_code == 204
    db_session.expire_all()
    assert db_session.get(Vulnerability, vuln.id) is not None
    # Promoting again makes a new finding — nothing still claims the row.
    again = client.post(
        f"/api/v1/projects/{test_project.id}/vulnerabilities/{vuln.id}/promote", json={"vuln_id": vuln.id},
    )
    assert again.status_code == 201 and again.json()["id"] != f["id"]


def test_a_comment_is_edited_and_deleted_by_its_author_only(client, test_project, test_user, people, act_as):
    f = _create(client, test_project)
    base = f"/api/v1/projects/{test_project.id}/findings/{f['id']}/notes"
    act_as(people["alice"])
    note = client.post(base, json={"body": "first draft"}).json()

    r = client.patch(f"{base}/{note['id']}", json={"body": "  corrected  "})
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "corrected"
    assert client.patch(f"{base}/{note['id']}", json={"body": "   "}).status_code == 422

    # Nobody else rewrites or removes it — a project admin and a global admin included.
    for other in (people["bob"], people["padmin"], test_user):
        act_as(other)
        assert client.patch(f"{base}/{note['id']}", json={"body": "hijack"}).status_code == 403
        assert client.delete(f"{base}/{note['id']}").status_code == 403

    act_as(people["alice"])
    assert client.delete(f"{base}/{note['id']}").status_code == 204
    assert [n["id"] for n in client.get(base).json()] == []


def test_a_comment_with_replies_is_kept(client, test_project, people, act_as):
    f = _create(client, test_project)
    base = f"/api/v1/projects/{test_project.id}/findings/{f['id']}/notes"
    act_as(people["alice"])
    root = client.post(base, json={"body": "root"}).json()
    act_as(people["bob"])
    reply = client.post(base, json={"body": "reply", "parent_id": root["id"]}).json()

    act_as(people["alice"])
    r = client.delete(f"{base}/{root['id']}")
    assert r.status_code == 409, r.text
    # The reply can go; then the root can.
    act_as(people["bob"])
    assert client.delete(f"{base}/{reply['id']}").status_code == 204
    act_as(people["alice"])
    assert client.delete(f"{base}/{root['id']}").status_code == 204


def test_a_comment_is_addressed_through_its_own_finding(client, test_project):
    a = _create(client, test_project, "A")
    b = _create(client, test_project, "B")
    note = client.post(f"/api/v1/projects/{test_project.id}/findings/{a['id']}/notes", json={"body": "on A"}).json()
    wrong = f"/api/v1/projects/{test_project.id}/findings/{b['id']}/notes/{note['id']}"
    assert client.patch(wrong, json={"body": "x"}).status_code == 404
    assert client.delete(wrong).status_code == 404
