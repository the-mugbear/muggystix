"""Adding hosts to a finding while reviewing it (POST /findings/{id}/hosts).

An analyst records that the issue affects more hosts: new endpoint rows start
open, a host already on the finding (host-level or as a named endpoint) is
skipped rather than given a second row, a host from another project fails the
whole request with nothing written, a viewer is refused, and the finding's
history names what was added.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import FindingHost, FindingStatusHistory
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.main import app


def _host(db_session, project_id: int, ip: str) -> models.Host:
    host = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(host)
    db_session.flush()
    return host


@pytest.fixture
def finding(client, db_session, test_project):
    first = _host(db_session, test_project.id, "10.20.0.1")
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": "SMB signing disabled", "severity": "high", "host_ids": [first.id]},
    )
    assert r.status_code == 201, r.text
    return {"id": r.json()["id"], "first": first}


def _url(project, fid):
    return f"/api/v1/projects/{project.id}/findings/{fid}/hosts"


def _rows(db_session, fid):
    return db_session.query(FindingHost).filter(FindingHost.finding_id == fid).all()


def test_adds_hosts_open_and_records_history(client, db_session, test_project, test_user, finding):
    a = _host(db_session, test_project.id, "10.20.0.2")
    b = _host(db_session, test_project.id, "10.20.0.3")
    db_session.commit()

    r = client.post(_url(test_project, finding["id"]), json={"host_ids": [b.id, a.id, a.id]})
    assert r.status_code == 200, r.text
    body = r.json()
    added = {h["host_id"]: h for h in body["hosts"]}
    assert set(added) == {finding["first"].id, a.id, b.id}
    assert added[a.id]["host_status"] == "open" and added[b.id]["host_status"] == "open"
    assert body["host_count"] == 3

    [row] = [
        h for h in db_session.query(FindingStatusHistory).filter_by(finding_id=finding["id"])
        if (h.summary or "").startswith("Added")
    ]
    assert row.summary == "Added 2 affected hosts: 10.20.0.2, 10.20.0.3"
    assert row.changed_by_id == test_user.id
    assert row.from_status == row.to_status == body["status"]  # the issue's status did not move


def test_a_host_already_attached_is_skipped(client, db_session, test_project, finding):
    new = _host(db_session, test_project.id, "10.20.0.4")
    db_session.commit()
    # A named endpoint on `new` counts as attached too: no second, unnamed row.
    name = models.DNSName(project_id=test_project.id, fqdn="www.example.com")
    db_session.add(name)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding["id"], host_id=new.id, name_id=name.id, host_status="retest"))
    db_session.commit()

    before = len(db_session.query(FindingStatusHistory).filter_by(finding_id=finding["id"]).all())
    r = client.post(_url(test_project, finding["id"]), json={"host_ids": [finding["first"].id, new.id]})
    assert r.status_code == 200, r.text
    rows = _rows(db_session, finding["id"])
    assert sorted((x.host_id, x.name_id) for x in rows) == sorted(
        [(finding["first"].id, None), (new.id, name.id)]
    )
    # Nothing was added, so nothing is written to the history either.
    assert len(db_session.query(FindingStatusHistory).filter_by(finding_id=finding["id"]).all()) == before


def test_a_host_from_another_project_fails_the_whole_request(client, db_session, test_project, finding):
    own = _host(db_session, test_project.id, "10.20.0.5")
    other = Project(id=97531, name="other", slug="other", created_at=datetime.now(timezone.utc))
    db_session.add(other)
    db_session.flush()
    foreign = _host(db_session, other.id, "192.168.77.7")
    db_session.commit()

    r = client.post(_url(test_project, finding["id"]), json={"host_ids": [own.id, foreign.id]})
    assert r.status_code == 422, r.text
    assert "192.168.77.7" not in r.text
    db_session.expire_all()
    assert [x.host_id for x in _rows(db_session, finding["id"])] == [finding["first"].id]
    assert not [
        h for h in db_session.query(FindingStatusHistory).filter_by(finding_id=finding["id"])
        if (h.summary or "").startswith("Added")
    ]


def test_more_than_500_hosts_is_refused(client, test_project, finding):
    r = client.post(_url(test_project, finding["id"]), json={"host_ids": list(range(1, 502))})
    assert r.status_code == 422


def test_a_viewer_cannot_add_hosts(client, db_session, test_project, finding):
    viewer = User(
        id=401, username="viewer-only", email="viewer-only@example.com", full_name="Viewer",
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(viewer)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=viewer.id, role=ProjectRole.VIEWER.value))
    new = _host(db_session, test_project.id, "10.20.0.6")
    db_session.commit()

    app.dependency_overrides[get_current_user] = lambda: viewer
    r = client.post(_url(test_project, finding["id"]), json={"host_ids": [new.id]})
    assert r.status_code == 403, r.text
    assert [x.host_id for x in _rows(db_session, finding["id"])] == [finding["first"].id]
