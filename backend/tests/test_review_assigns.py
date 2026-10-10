"""Taking a host into review makes it 'yours'.

Operators read "I set this host In Review" as "this host is mine", so the
review-status write path stamps assigned_at (the signal the "Assigned to me"
filter keys on) — mirroring the explicit Assign action.  Watching does not.
"""
from app.db import models
from app.db.models import FollowStatus
from app.services.host_follow_service import HostFollowService


def _host(db, project_id, ip):
    h = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(h)
    db.flush()
    return h


def test_in_review_stamps_ownership(db_session, test_project, test_user):
    host = _host(db_session, test_project.id, "10.5.5.5")
    follow = HostFollowService(db_session).set_follow_status(
        host.id, test_user.id, FollowStatus.IN_REVIEW
    )
    assert follow.assigned_at is not None
    assert follow.assigned_by_id == test_user.id


def test_reviewed_stamps_ownership(db_session, test_project, test_user):
    host = _host(db_session, test_project.id, "10.5.5.6")
    follow = HostFollowService(db_session).set_follow_status(
        host.id, test_user.id, FollowStatus.REVIEWED
    )
    assert follow.assigned_at is not None


def test_watching_does_not_assign(db_session, test_project, test_user):
    host = _host(db_session, test_project.id, "10.5.5.7")
    follow = HostFollowService(db_session).set_follow_status(
        host.id, test_user.id, FollowStatus.WATCHING
    )
    assert follow.assigned_at is None


def test_the_peoples_review_status_refuses_watching(client, db_session, test_project, test_user):
    """No control can set the retired ``watching``, so the routes do not take
    it either: the single-host and the bulk route accept exactly the review
    states the pages offer — the same type the agents' route is built from —
    and anything else is a 422 naming them.  A row that already holds
    ``watching`` is still read, and still cleared by the page's DELETE."""
    from typing import get_args

    from app.api.v1.endpoints.agent_schemas import AgentFollowStatus, FOLLOW_CLEAR
    from app.main import app
    from app.schemas.schemas import ReviewStateToSet
    from app.services.engagement_metrics_service import REVIEW_STATES

    accepted = [s.value for s in REVIEW_STATES]
    assert list(get_args(ReviewStateToSet)) == accepted == ["in_review", "reviewed"]
    assert list(get_args(AgentFollowStatus)) == accepted + [FOLLOW_CLEAR]

    host = _host(db_session, test_project.id, "10.5.5.8")
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/hosts"
    one, bulk = f"{base}/{host.id}/follow", f"{base}/bulk/follow"

    def stored():
        db_session.expire_all()
        return [f.status for f in db_session.query(models.HostFollow).filter_by(host_id=host.id)]

    # What the page sends keeps working: set, set, clear.
    for value in accepted:
        r = client.post(one, json={"status": value})
        assert r.status_code == 200 and r.json()["status"] == value, r.text
        assert stored() == [FollowStatus(value)]
    r = client.post(bulk, json={"host_ids": [host.id], "status": "in_review"})
    assert r.status_code == 200 and r.json()["affected"] == 1, r.text
    assert stored() == [FollowStatus.IN_REVIEW]

    for refused in ("watching", "bogus", "none"):
        for url, body in ((one, {"status": refused}), (bulk, {"host_ids": [host.id], "status": refused})):
            r = client.post(url, json=body)
            assert r.status_code == 422, (url, refused, r.status_code, r.text)
            for value in accepted:
                assert f"'{value}'" in r.text, (refused, r.text)
        assert stored() == [FollowStatus.IN_REVIEW]

    assert client.delete(one).status_code in (200, 204)
    assert stored() == []

    # A row from before the state was retired: read as it is, not settable,
    # cleared like any other.
    db_session.add(models.HostFollow(host_id=host.id, user_id=test_user.id, status=FollowStatus.WATCHING))
    db_session.commit()
    read = client.get(f"{base}/{host.id}")
    assert read.status_code == 200 and read.json()["follow"]["status"] == "watching", read.text
    assert client.post(one, json={"status": "watching"}).status_code == 422
    assert stored() == [FollowStatus.WATCHING]
    assert client.delete(one).status_code in (200, 204)
    assert stored() == []

    schemas = app.openapi()["components"]["schemas"]
    assert schemas["HostFollowUpdate"]["properties"]["status"]["enum"] == accepted
    assert schemas["BulkFollowRequest"]["properties"]["status"]["enum"] == accepted
