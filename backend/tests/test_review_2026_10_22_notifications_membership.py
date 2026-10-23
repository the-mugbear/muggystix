"""Review 2026-10-22, A-R3 — a project's notifications only while a member.

The bell's reads filtered by recipient alone, and removing a member touched no
notification: a removed member kept listing and paging through the project's
finding titles, assignments and mentions (a mention stores the first 200
characters of the note).  ``NotificationService._shown`` now keeps a
project's notifications for its current members and global administrators;
one with no project stays its recipient's.  Nothing is deleted: re-added, the
member sees them again.
"""
from datetime import datetime, timezone

from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User, UserRole
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.main import app
from app.services.notification_service import NotificationService


def _user(db, uid, name, role=UserRole.MEMBER):
    u = User(id=uid, username=name, email=f"{name}@example.com", full_name=name,
             hashed_password="x", role=role, is_active=True, created_at=datetime.now(timezone.utc))
    db.add(u)
    db.flush()
    return u


def _notify(db, user, project_id, title):
    n = Notification(user_id=user.id, project_id=project_id, type="mention", title=title,
                     body="the first 200 characters of a note")
    db.add(n)
    db.flush()
    return n


def test_a_removed_member_no_longer_reads_the_projects_notifications(db_session, test_project):
    member = _user(db_session, 501, "leaver")
    membership = ProjectMembership(project_id=test_project.id, user_id=member.id, role=ProjectRole.ANALYST.value)
    db_session.add(membership)
    _notify(db_session, member, test_project.id, "mentioned on the project")
    _notify(db_session, member, None, "about the account")
    db_session.commit()
    svc = NotificationService(db_session)

    assert {n.title for n in svc.get_notifications(member.id)} == {"mentioned on the project", "about the account"}
    assert svc.get_unread_count(member.id) == 2

    db_session.delete(membership)
    db_session.commit()
    assert [n.title for n in svc.get_notifications(member.id)] == ["about the account"]
    assert svc.get_unread_count(member.id) == 1
    assert svc.mark_all_read(member.id) == 1
    db_session.commit()
    project_row = db_session.query(Notification).filter(Notification.project_id == test_project.id).one()
    assert project_row.is_read is False, "mark-all-read covers only what is shown"

    # Re-added, the notification is back (nothing was deleted).
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role=ProjectRole.VIEWER.value))
    db_session.commit()
    assert svc.get_unread_count(member.id) == 1
    assert "mentioned on the project" in {n.title for n in svc.get_notifications(member.id)}


def test_a_global_admin_reads_every_projects_notifications_without_a_membership(db_session, test_project):
    admin = _user(db_session, 502, "global-admin", role=UserRole.ADMIN)
    _notify(db_session, admin, test_project.id, "remediation overdue")
    db_session.commit()
    assert NotificationService(db_session).get_unread_count(admin.id) == 1


def test_the_bell_route_hides_a_former_projects_notifications(client, db_session, test_project):
    member = _user(db_session, 503, "routed")
    _notify(db_session, member, test_project.id, "assigned to you")
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: member
    try:
        r = client.get("/api/v1/notifications/")
        assert r.status_code == 200, r.text
        body = r.json()
        rows = body["notifications"] if isinstance(body, dict) else body
        assert rows == []
        assert client.get("/api/v1/notifications/unread-count").json()["unread_count"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
