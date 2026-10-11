"""Owner, 2026-10-11 (review 2026-10-10, S2) — setting a host's review status
is "an operation that viewers should not be able to do": it needs project
ANALYST on the people's door, as it always has on the agents'.

The single route (set and clear), the bulk route and "Still reviewed" share
the floor.  READING review state — the row's ``follow``, ``follow:`` in a
query — stays every member's.
"""
import pytest

from app.api.deps import get_current_user
from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership
from app.main import app


@pytest.fixture()
def world(client, db_session, test_project):
    hosts = []
    for last in (1, 2, 3):
        host = models.Host(project_id=test_project.id, ip_address=f"10.96.0.{last}", state="up")
        db_session.add(host)
        hosts.append(host)
    people = {}
    for uid, role in ((96001, "viewer"), (96002, "auditor"), (96003, "analyst"), (96004, "admin")):
        user = User(id=uid, username=f"{role}-{uid}", email=f"{role}-{uid}@example.com",
                    hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True)
        db_session.add(user)
        db_session.flush()
        db_session.add(ProjectMembership(project_id=test_project.id, user_id=user.id, role=role))
        people[role] = user
    db_session.flush()
    # A teammate's review, for the readers to read.
    db_session.add(HostFollow(host_id=hosts[2].id, user_id=people["analyst"].id, status=FollowStatus.IN_REVIEW))
    db_session.commit()
    return {"base": f"/api/v1/projects/{test_project.id}", "hosts": [h.id for h in hosts], "people": people}


@pytest.fixture()
def acting_as(client):
    """``acting_as(user)`` — later requests of this test are that person's."""
    def switch(user):
        app.dependency_overrides[get_current_user] = lambda: user
    return switch


def _attempts(client, world):
    base, (first, second, _third) = world["base"], world["hosts"]
    return {
        "set": client.post(f"{base}/hosts/{first}/follow", json={"status": "in_review"}),
        "clear": client.delete(f"{base}/hosts/{first}/follow"),
        "bulk": client.post(f"{base}/hosts/bulk/follow", json={"host_ids": [first, second], "status": "reviewed"}),
        "still reviewed": client.post(f"{base}/workbench/followups/still-reviewed", json={"host_ids": [first]}),
    }


@pytest.mark.parametrize("role", ["viewer", "auditor"])
def test_a_reader_cannot_set_or_clear_a_review_status(client, db_session, world, acting_as, role):
    user = world["people"][role]
    acting_as(user)
    for name, resp in _attempts(client, world).items():
        assert resp.status_code == 403, f"{role} {name}: {resp.status_code} {resp.text[:200]}"
    assert db_session.query(HostFollow).filter(HostFollow.user_id == user.id).count() == 0


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_a_writer_sets_and_clears_as_before(client, db_session, world, acting_as, role):
    user = world["people"][role]
    acting_as(user)
    base, (first, second, _third) = world["base"], world["hosts"]

    started = client.post(f"{base}/hosts/{first}/follow", json={"status": "in_review"})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_review"
    assert client.delete(f"{base}/hosts/{first}/follow").status_code == 204
    bulk = client.post(f"{base}/hosts/bulk/follow", json={"host_ids": [first, second], "status": "reviewed"})
    assert bulk.status_code == 200 and bulk.json()["affected"] == 2
    again = client.post(f"{base}/workbench/followups/still-reviewed", json={"host_ids": [first]})
    assert again.status_code == 200, again.text
    db_session.expire_all()
    mine = db_session.query(HostFollow).filter(
        HostFollow.user_id == user.id, HostFollow.host_id.in_([first, second]),
    ).all()
    assert sorted(str(getattr(f.status, "value", f.status)) for f in mine) == ["reviewed", "reviewed"]


@pytest.mark.parametrize("role", ["viewer", "auditor"])
def test_a_reader_still_reads_review_state(client, world, acting_as, role):
    acting_as(world["people"][role])
    base, (_first, _second, third) = world["base"], world["hosts"]

    def ips(**params):
        resp = client.get(f"{base}/hosts/", params=params)
        assert resp.status_code == 200, resp.text
        return [h["ip_address"] for h in resp.json()["items"]]

    assert ips(q="follow:in_review") == ["10.96.0.3"]
    assert ips(follow_status="in_review_any") == ["10.96.0.3"]
    assert ips(q="follow:mine") == []
    assert ips(q="assigned:me") == []
    assert len(ips(follow_status="none")) == 2
    followers = client.get(f"{base}/hosts/{third}/followers")
    assert followers.status_code == 200
    assert [f["status"] for f in followers.json()["followers"]] == ["in_review"]
    # Opening a host is not a review write.
    assert client.post(f"{base}/hosts/{third}/view").status_code == 204
