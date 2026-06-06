"""RV-6 — Activity 'seen' cursor is per (user, project), not global.

Marking project A's activity feed seen must NOT hide unread activity in
project B.  ``client`` authenticates as a global admin (id=1), so notes
authored by a *different* user count toward its unread total.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models import HostNote, NoteStatus
from app.db.models_project import Project

_UID = [5000]


def _make_user(db_session, username):
    from app.db.models_auth import User, UserRole
    _UID[0] += 1
    u = User(
        id=_UID[0], username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u)
    db_session.flush()
    return u


def _make_host(db_session, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(h)
    db_session.flush()
    return h


def _unread(client, pid):
    return client.get(f"/api/v1/projects/{pid}/hosts/notes/unread-count").json()["unread_count"]


def test_cursor_upsert_is_monotonic(db_session, test_project, test_user):
    """CR3-#7 — a late older write must not move the cursor backward."""
    from datetime import datetime, timezone, timedelta
    from app.db.cursor_upsert import upsert_user_project_cursor
    from app.db.models import OperationsCursor

    t1 = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
    t0 = t1 - timedelta(hours=1)

    upsert_user_project_cursor(
        db_session, OperationsCursor, user_id=test_user.id, project_id=test_project.id,
        ts_column="last_viewed_at", ts_value=t1,
    )
    # An out-of-order OLDER write lands last — must be ignored.
    upsert_user_project_cursor(
        db_session, OperationsCursor, user_id=test_user.id, project_id=test_project.id,
        ts_column="last_viewed_at", ts_value=t0,
    )
    row = (
        db_session.query(OperationsCursor)
        .filter(OperationsCursor.user_id == test_user.id,
                OperationsCursor.project_id == test_project.id)
        .one()
    )
    assert row.last_viewed_at.replace(tzinfo=None) == t1.replace(tzinfo=None)


def test_activity_seen_does_not_leak_across_projects(client, db_session, test_project):
    proj_b = Project(name="rv6-proj-b", slug="rv6-proj-b")
    db_session.add(proj_b)
    db_session.flush()

    other = _make_user(db_session, "rv6-teammate")
    host_a = _make_host(db_session, test_project.id, "10.6.0.1")
    host_b = _make_host(db_session, proj_b.id, "10.6.1.1")
    db_session.add_all([
        HostNote(host_id=host_a.id, user_id=other.id, body="A note", status=NoteStatus.OPEN),
        HostNote(host_id=host_b.id, user_id=other.id, body="B note", status=NoteStatus.OPEN),
    ])
    db_session.flush()

    # Both projects start with an unread teammate note.
    assert _unread(client, test_project.id) >= 1
    assert _unread(client, proj_b.id) >= 1

    # Mark ONLY project A seen.
    r = client.post(f"/api/v1/projects/{test_project.id}/hosts/notes/mark-seen")
    assert r.status_code == 204, r.text

    # A is cleared; B is untouched (pre-fix the shared cursor zeroed both).
    assert _unread(client, test_project.id) == 0
    assert _unread(client, proj_b.id) >= 1
