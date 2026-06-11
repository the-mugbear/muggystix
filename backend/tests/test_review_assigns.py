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
