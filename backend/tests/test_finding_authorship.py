"""v2.375.0 — who may change a finding, and a comment.

A finding's title and report text are any project analyst's (2.519.0; they
were the author's and a project admin's); the finding itself (delete) only
its author's or a project admin's; a comment's text only the comment's
author's.  Triage (severity, owner, status) is any analyst's.
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
        is_active=True, created_at=datetime.now(timezone.utc),
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


def test_any_analyst_may_rename_and_a_reader_may_not(client, db_session, test_project, people, act_as):
    """2.519.0 (owner 2026-10-21): any analyst may set a finding's owner, so
    the title and report text are any analyst's too — not only the author's."""
    act_as(people["alice"])
    f = _create(client, test_project)
    assert f["created_by_id"] == people["alice"].id
    assert f["created_by_name"] == "Alice"
    assert (f["can_modify"], f["can_delete"]) == (True, True)
    url = f"/api/v1/projects/{test_project.id}/findings/{f['id']}"

    r = client.patch(url, json={"title": "  SMB signing not required  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "SMB signing not required"

    # Bob neither recorded it nor owns it.
    act_as(people["bob"])
    row = client.get(url).json()
    assert (row["owner_id"], row["can_modify"], row["can_delete"]) == (None, True, False)
    r = client.patch(url, json={"title": "SMB signing off", "description": "Bob's words"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "SMB signing off"
    assert r.json()["report_text"]["description"] == "Bob's words"

    auditor = _member(db_session, test_project, 204, "audrey", ProjectRole.AUDITOR)
    act_as(auditor)
    assert client.get(url).json()["can_modify"] is False
    assert client.patch(url, json={"title": "Audrey's title"}).status_code == 403

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

    # A finding made from a note, when notes could be promoted (before
    # v2.446.0): the link is what must survive the deletion.
    legacy = Finding(project_id=test_project.id, title="Anon SMB", severity="high", status="confirmed",
                     source="note", evidence_annotation_id=source_note.id, created_by_id=people["alice"].id)
    db_session.add(legacy)
    db_session.flush()
    db_session.add(FindingHost(finding_id=legacy.id, host_id=host.id))
    db_session.commit()
    fid = legacy.id

    act_as(people["alice"])
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
    # The source note survives, on its host.
    assert db_session.get(Annotation, source_note.id) is not None
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
    assert client.get(base).json() == {"items": [], "total": 0}


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


def test_the_owner_may_not_delete_a_finding_someone_else_recorded(client, test_project, people, act_as):
    """An owner is assigned by someone else: being given a finding is not
    being allowed to remove it."""
    act_as(people["alice"])
    f = _create(client, test_project)
    url = f"/api/v1/projects/{test_project.id}/findings/{f['id']}"
    assert client.patch(url, json={"owner_id": people["bob"].id}).status_code == 200

    act_as(people["bob"])
    assert client.get(url).json()["can_delete"] is False
    r = client.delete(url)
    assert r.status_code == 403 and "author or a project admin can delete" in r.json()["detail"]
