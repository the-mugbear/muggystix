"""2.519.0 — a finding's history records each edit, with the value before and after.

The owner, 2026-10-21: any project analyst may edit a finding, and a direct
edit of its title, severity, owner or report text was recorded nowhere.
Each now writes ONE line on the finding's history (`finding_actions.
record_edit`), naming the field; `GET …/history/{id}` returns the field's
value before and after.  An unchanged value resent by a form writes nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
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
def people(db_session, test_project):
    people = {
        "alice": _member(db_session, test_project, 501, "alice", ProjectRole.ANALYST),
        "bob": _member(db_session, test_project, 502, "bob", ProjectRole.ANALYST),
    }
    app.dependency_overrides[get_current_user] = lambda: people["alice"]
    yield people
    app.dependency_overrides.pop(get_current_user, None)


def _url(project, fid=None):
    base = f"/api/v1/projects/{project.id}/findings"
    return base if fid is None else f"{base}/{fid}"


def _edits(client, project, fid):
    return [h for h in client.get(f"{_url(project, fid)}/history").json() if h["field"]]


def _values(client, project, fid, entry):
    r = client.get(f"{_url(project, fid)}/history/{entry['id']}")
    assert r.status_code == 200, r.text
    return r.json()["before"], r.json()["after"]


def test_each_edited_field_gets_a_line_with_its_before_and_after(client, test_project, people):
    fid = client.post(_url(test_project), json={"title": "Weak TLS", "severity": "medium"}).json()["id"]
    url = _url(test_project, fid)
    assert _edits(client, test_project, fid) == []

    r = client.patch(url, json={
        "title": "TLS 1.0 enabled", "severity": "high", "owner_id": people["bob"].id,
        "impact": "Traffic can be read.",
    })
    assert r.status_code == 200, r.text
    edits = {e["field"]: e for e in _edits(client, test_project, fid)}
    assert set(edits) == {"title", "severity", "owner", "impact"}
    assert edits["title"]["summary"] == "Renamed"
    assert edits["severity"]["summary"] == "Severity: Medium → High"
    assert edits["owner"]["summary"] == "Owner: none → Bob"
    assert edits["impact"]["summary"] == "Impact written"
    assert all(e["changed_by_name"] == "Alice" for e in edits.values())
    assert _values(client, test_project, fid, edits["title"]) == ("Weak TLS", "TLS 1.0 enabled")
    assert _values(client, test_project, fid, edits["severity"]) == ("Medium", "High")
    assert _values(client, test_project, fid, edits["owner"]) == (None, "Bob")
    assert _values(client, test_project, fid, edits["impact"]) == (None, "Traffic can be read.")

    # Another analyst rewrites the section, then clears it: each is a line.
    app.dependency_overrides[get_current_user] = lambda: people["bob"]
    client.patch(url, json={"impact": "Sessions can be decrypted."})
    client.patch(url, json={"impact": ""})
    impact = [e for e in _edits(client, test_project, fid) if e["field"] == "impact"]  # newest first
    assert [e["summary"] for e in impact] == ["Impact cleared", "Impact edited", "Impact written"]
    assert impact[1]["changed_by_name"] == "Bob"
    assert _values(client, test_project, fid, impact[1]) == ("Traffic can be read.", "Sessions can be decrypted.")
    assert _values(client, test_project, fid, impact[0]) == ("Sessions can be decrypted.", None)


def test_an_unchanged_value_resent_writes_nothing(client, test_project, people):
    fid = client.post(_url(test_project), json={"title": "Weak TLS", "severity": "medium"}).json()["id"]
    url = _url(test_project, fid)
    client.patch(url, json={"description": "Seen.", "owner_id": people["bob"].id})
    before = len(_edits(client, test_project, fid))
    r = client.patch(url, json={
        "title": "Weak TLS", "severity": "medium", "description": "Seen.", "owner_id": people["bob"].id,
    })
    assert r.status_code == 200, r.text
    assert len(_edits(client, test_project, fid)) == before


def test_the_bulk_assign_records_each_findings_owner(client, test_project, people):
    ids = [client.post(_url(test_project), json={"title": t, "severity": "low"}).json()["id"] for t in ("A", "B")]
    r = client.post(f"{_url(test_project)}/bulk/assign", json={
        "finding_ids": ids, "assignee_user_id": people["bob"].id})
    assert r.status_code == 200, r.text
    for fid in ids:
        (line,) = _edits(client, test_project, fid)
        assert (line["field"], line["summary"]) == ("owner", "Owner: none → Bob")


def test_a_status_line_has_no_values_to_read(client, test_project, people):
    fid = client.post(_url(test_project), json={"title": "Weak TLS", "severity": "medium"}).json()["id"]
    client.post(f"{_url(test_project, fid)}/status", json={"status": "confirmed"})
    line = client.get(f"{_url(test_project, fid)}/history").json()[0]  # newest: the status move
    assert (line["to_status"], line["field"]) == ("confirmed", None)
    assert client.get(f"{_url(test_project, fid)}/history/{line['id']}").status_code == 404
    # Nor is another finding's line readable through this one.
    other = client.post(_url(test_project), json={"title": "Other", "severity": "low"}).json()["id"]
    client.patch(_url(test_project, other), json={"title": "Other one"})
    (edit,) = _edits(client, test_project, other)
    assert client.get(f"{_url(test_project, fid)}/history/{edit['id']}").status_code == 404
